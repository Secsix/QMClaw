"""Integrated S21ZPA recognition -> interval search -> qubit calibration.

2026-11-13 改造：实验调用改为 **`.qwf` 驱动**（默认 `execution_route='qwf'`），
调用口径仍是**旧版 legacy**：`sq.s21_zpa2d(qobj)` / `sq.spectroscopy(qobj, ...)` /
`sq.iqraw(qobj)` / `sq.piamp(qobj, amp=2)` / `sq.pidf(qobj, df=0.08)` /
`sq.ramsey_df(qobj)` / `sq.s21_dis(qobj)` / `sq.set_pi(qobj, gate='X')` /
`sq.set_pi(qobj, gate='X/2')` / `sq.t1(qobj, zpa=selected_bias)`。

执行层在 `zpa_qwf_runner.py`：
  * `qwf`（默认）：实验写成 `.qwf` → :3008 `/workflows/run_ir` → workflow_service
    → quantum_service；`.qwf` 下发出来就是 `sq.<fn>(<qubit>, **args)`，与旧版
    直调等价。寄存器写（`bias_z`）走 `:3003/qubit/set_params`（`.qwf` 没有写
    寄存器的节点）。
  * `direct`：保留旧版行为，在本进程里直调 `sq.<fn>(qobj, **args)`。

Default CLI is OFFLINE. Hardware execution requires an explicit trusted adapter
and --execute. No LabRAD auto-connect and no inference of experiment return APIs.
"""
from __future__ import annotations
import argparse,csv,importlib,json,math,time,traceback
from dataclasses import dataclass,asdict,field
from datetime import datetime,timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional
import numpy as np
from classify_zpa_datavault import load_model
from plan_zpa_bias import plan
from zpa_qwf_runner import DirectRunner,QwfRunner,step

@dataclass
class Settings:
    bias_step: float = .2
    visibility_threshold: float = .05
    spec_start: float = 5.5
    spec_stop: float = 4.0
    spec_step: float = -.001
    spec_amp: float = .1
    piamp_amp: float = 2.
    min_prominence_db: Optional[float] = None   # None = 用训练出来的模型（zpa_interval_model.json）
    min_interval_points: Optional[int] = None
    interval_tol_mhz: Optional[float] = None      # None = 用训练出来的模型
    # ── 执行路线 ──
    execution_route: str = 'qwf'          # 'qwf'（默认）| 'direct'（旧版进程内直调）
    workflow_base: str = 'http://127.0.0.1:3008'
    quantum_base: str = 'http://127.0.0.1:3003'
    # ── 实验参数（全部显式写死，避免厂商默认展开出几百点）──
    acquire_s21zpa: bool = True           # 真机执行先现场采一张 S21ZPA 地图
    search_update: bool = False           # bias 遍历阶段只采不写（避免 f10 漂移）
    zpa_lo: float = -3.
    zpa_hi: float = 3.1
    zpa_step: float = .2
    zpa_freq_span: float = .002
    pidf_df: float = .08
    t1_delay_stop: float = 84000.
    t1_delay_step: float = 6000.
    ramsey_t2_delay_stop: float = 40000.
    ramsey_t2_delay_step: float = 4000.
    xeb_m: int = 3
    xeb_k: int = 10
    xeb_stats: int = 300

    def validate(self):
        if self.execution_route not in ('qwf','direct'):raise ValueError("execution_route must be 'qwf' or 'direct'")
        vals=list(asdict(self).values())
        if not all(math.isfinite(float(x)) for x in vals if isinstance(x,(int,float)) and not isinstance(x,bool)):raise ValueError('Non-finite settings')
        if self.bias_step<=0 or self.spec_step>=0 or self.spec_start<=self.spec_stop:raise ValueError('Invalid bias/frequency steps')
        if self.spec_amp<=0 or self.piamp_amp<=0:raise ValueError('Invalid amplitude settings')
        if self.min_prominence_db is not None and self.min_prominence_db<=0:raise ValueError('Invalid min_prominence_db')
        if self.min_interval_points is not None and self.min_interval_points<2:raise ValueError('Invalid min_interval_points')
        if self.interval_tol_mhz is not None and self.interval_tol_mhz<=0:raise ValueError('Invalid interval tolerance')
        if not 0<=self.visibility_threshold<=1:raise ValueError('Visibility threshold must be in [0,1]')
        if len(np.arange(self.spec_start,self.spec_stop,self.spec_step))>100000:raise ValueError('Too many spectroscopy points')
        if self.zpa_step<=0 or self.zpa_hi<=self.zpa_lo:raise ValueError('Invalid zpa range')
        if self.t1_delay_step<=0 or self.t1_delay_stop<=0:raise ValueError('Invalid t1 delay range')
        if self.ramsey_t2_delay_step<=0 or self.ramsey_t2_delay_stop<=0:raise ValueError('Invalid ramsey_t2 delay range')
        if self.xeb_m<=0 or self.xeb_k<=0 or self.xeb_stats<=0:raise ValueError('Invalid xeb parameters')


def utcnow():return datetime.now(timezone.utc).isoformat()

def serializable(x):
    if isinstance(x,np.ndarray):return serializable(x.tolist())
    if isinstance(x,np.generic):return serializable(x.item())
    if isinstance(x,float) and not math.isfinite(x):return None
    if isinstance(x,Path):return str(x)
    if isinstance(x,dict):return {str(k):serializable(v) for k,v in x.items()}
    if isinstance(x,(list,tuple)):return [serializable(v) for v in x]
    if x is None or isinstance(x,(str,int,float,bool)):return x
    return {'unserialized_type':type(x).__name__}

def save_json(path,data):
    path=Path(path);path.parent.mkdir(exist_ok=True,parents=True)
    # Single runner only. Atomic replace to retain last valid checkpoint.
    tmp=path.with_name(path.name+'.tmp');tmp.write_text(json.dumps(serializable(data),ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8');tmp.replace(path)

def bias_grid(intervals,step=.2):
    """Each interval starts at its own minimum; never steps outside or across gaps."""
    if not math.isfinite(step) or step<=0:raise ValueError('Invalid step')
    result=set();d=Decimal(str(step))
    for lo,hi in intervals:
        if not all(math.isfinite(float(v)) for v in [lo,hi]) or lo>hi:raise ValueError('Invalid interval')
        # Remove binary noise from HDF5 np.arange coordinates.
        low=Decimal(str(round(float(lo),10)));high=Decimal(str(round(float(hi),10)))
        if (high-low)/d>10000:raise ValueError('Too many bias points')
        n=int((high-low)//d)
        for i in range(n+1):result.add(float(low+i*d))
    return sorted(result)

def inspect_file(path,settings=None,model=None):
    settings=settings or Settings();settings.validate()
    r=plan(Path(path),model or load_model(),settings.min_prominence_db,settings.min_interval_points,
        tol_mhz=settings.interval_tol_mhz)
    allowed=r['file_normal_prediction'] and not r['file_review_required']
    intervals=[i['sampled_zpa_interval'] for i in r['intervals']] if allowed else []
    r['scan_bias_values']=bias_grid(intervals,settings.bias_step)
    r['pipeline_eligible']=bool(r['scan_bias_values'])
    r['bias_mapping']='qobj.regs.bias_z = scanned_zpa (user-confirmed)'
    r['execution_enabled']=False
    r['status']='ready_for_preflight' if r['pipeline_eligible'] else 'skipped_no_reliable_interval'
    return r


def metric_number(value,name,lo=None,hi=None):
    if isinstance(value,(bool,np.bool_)) or not isinstance(value,(float,int,np.number)):raise ValueError(name+' must be an explicit numeric metric')
    x=float(value)
    if not math.isfinite(x) or lo is not None and x<lo or hi is not None and x>hi:raise ValueError('Invalid '+name)
    return x


@dataclass
class Context:
    """一次比特标定的执行上下文（两条路线共用）。"""
    bit: str
    session: Any = None
    qobj: Any = None
    sq: Any = None
    runner: Any = None


def build_context(adapter,bit,settings,outdir,log=print,dry=False):
    """按 execution_route 建上下文：qwf 路线拿 (bit, session)，direct 路线拿 (qobj, sq)。"""
    if settings.execution_route=='qwf':
        got=adapter.get_context(bit)
        if not isinstance(got,(tuple,list)) or len(got)!=2:raise TypeError('qwf 路线的 get_context(bit) 必须返回 (bit, session)')
        name,session=got
        if name!=bit:raise ValueError('adapter 返回的比特名与请求不一致')
        runner=QwfRunner(bit=bit,session=session,datavault_root=Path(adapter.datavault_root),
                         outdir=Path(outdir),workflow_base=settings.workflow_base,dry=dry,log=log)
        return Context(bit=bit,session=session,qobj=None,sq=None,runner=runner)
    qobj,sq=adapter.get_context(bit)
    return Context(bit=bit,session=None,qobj=qobj,sq=sq,runner=DirectRunner(sq,qobj,log=log))


def preflight(adapter,ctx,biases,settings):
    common=['preflight','snapshot','restore','safe_shutdown','maximum_visibility','piamp_excited','final_metrics','apply_bias','read_bias']
    if settings.execution_route=='direct':common+=['wait_for_bias','latest_datavault_path']
    for name in common:
        if not callable(getattr(adapter,name,None)):raise TypeError('Adapter missing '+name)
    if settings.execution_route=='direct':
        for name in ['s21_zpa2d','spectroscopy','iqraw','piamp','pidf','ramsey_df','s21_dis','set_pi','t1']:
            if not callable(getattr(ctx.sq,name,None)):raise TypeError('sq missing '+name)
    permission=adapter.preflight(ctx.bit,ctx,settings,biases)
    # Adapter must check wiring, units, amplitudes, frequency limits, bias ramp,
    # settling time, readback, and rollback. Never default unspecified limits.
    if not isinstance(permission,dict) or permission.get('approved') is not True:raise ValueError('Preflight not approved')
    lo,hi=permission['bias_limits'];lo=metric_number(lo,'lower bias limit');hi=metric_number(hi,'upper bias limit')
    if lo>=hi or any(not lo<=b<=hi for b in biases):raise ValueError('Planned bias outside approved bounds')
    if permission.get('frequency_unit')!='GHz':raise ValueError('Spectroscopy frequency unit must be confirmed GHz')
    if permission.get('bias_mapping_confirmed') is not True:raise ValueError('bias_z mapping not confirmed')
    if permission.get('t1_zpa_is_selected_bias') is not True:raise ValueError('Confirm t1 zpa parameter means selected absolute bias')
    return permission


def acquire_s21zpa(ctx,settings,outdir,log=print):
    """现场采一张 S21ZPA 地图（首步），并绑定**本次调用**产生的 DataVault 结果。

    返回 (hdf5 路径, 原始结果 dict)。找不到本次结果就报错 —— 绝不用历史文件顶替。
    """
    steps=[step('z1','sq.s21_zpa2d',
                {'zpa':{'$arange':[settings.zpa_lo,settings.zpa_hi,settings.zpa_step]},
                 'freq_span':settings.zpa_freq_span,'update':False},
                timeout=1800,note=f"{ctx.bit} 现场 S21ZPA 地图（只采不写）")]
    results=ctx.runner.batch(steps,name=f'{ctx.bit}_s21zpa2d',
                              description=f'{ctx.bit} 现场 S21ZPA 地图（bias 区间来源）',
                              max_wall='40m')
    result=results[0]
    if not result.get('path'):
        raise ValueError('S21ZPA 没有绑定本次调用的 DataVault 结果（不能拿历史文件顶替）')
    log(f"    S21ZPA 数据集 {result.get('dataset_id')} → {result['path']}")
    return result['path'],result


def run_bit(bit,ctx,adapter,inspection,outdir,settings=None,model=None,dry=False):
    """Hardware-capable API. Call only with authorized, validated live adapter.
    On success, keep the selected bias and calibration. On failure, shut down
    drive and restore adapter snapshot. No automatic retry or process timeout.
    """
    settings=settings or Settings();settings.validate();outdir=Path(outdir);outdir.mkdir(exist_ok=True,parents=True)
    result=dict(bit=bit,source_file=inspection['file'],source_id=inspection['id'],started_at=utcnow(),status='pending',attempts=[],calibration=[],metrics=None,selected_bias_z=None,settings=asdict(settings),execution_route=settings.execution_route)
    checkpoint=outdir/(bit+'_result.json')
    def save():save_json(checkpoint,result)
    def event(name,state,**extra):
        with (outdir/(bit+'_events.jsonl')).open('a',encoding='utf-8') as f:
            f.write(json.dumps(serializable(dict(time=utcnow(),operation=name,state=state,**extra)),ensure_ascii=False)+'\n')
    def log(msg):print(msg,flush=True)
    biases=inspection.get('scan_bias_values',[])
    try:
        permission=preflight(adapter,ctx,biases,settings)
        result['preflight']=permission;snapshot=adapter.snapshot(ctx);save()
    except Exception as exc:
        result['status']='error';result['error']=dict(type=type(exc).__name__,message=str(exc));save();return result
    success=False
    try:
        # ── 第 0 步：现场采 S21ZPA（真机执行时）──
        if settings.acquire_s21zpa:
            event('s21_zpa2d','started')
            if dry:
                # 干跑：只生成 + 校验 S21ZPA 的 .qwf，不下发、不取数据集
                steps=[step('z1','sq.s21_zpa2d',
                            {'zpa':{'$arange':[settings.zpa_lo,settings.zpa_hi,settings.zpa_step]},
                             'freq_span':settings.zpa_freq_span,'update':False},
                            timeout=1800,note=f"{bit} 现场 S21ZPA 地图（只采不写）")]
                ctx.runner.batch(steps,name=f'{bit}_s21zpa2d',
                                 description=f'{bit} 现场 S21ZPA 地图（bias 区间来源）',
                                 max_wall='40m')
                event('s21_zpa2d','validated')
            else:
                path,zpa_result=acquire_s21zpa(ctx,settings,outdir,log)
                event('s21_zpa2d','completed',dataset=zpa_result.get('dataset_id'),path=path)
                fresh=inspect_file(path,settings,model)
                if fresh.get('bit')!=bit:raise ValueError('S21ZPA 文件里的比特名与目标比特不一致')
                if not fresh.get('pipeline_eligible'):raise ValueError('现场 S21ZPA 没找到可靠区间，停止后续实验')
                result['live_s21zpa']=dict(dataset=zpa_result.get('dataset_id'),path=path,source_file=fresh['file'],
                                           scan_bias_values=fresh['scan_bias_values'])
                result['source_file']=fresh['file'];result['source_id']=fresh['id']
                biases=fresh['scan_bias_values'];save()
                # 新区间必须仍在 preflight 批准的 bias 上下限内
                lo,hi=permission['bias_limits']
                if any(not float(lo)<=b<=float(hi) for b in biases):
                    raise ValueError('现场 S21ZPA 得到的 bias 点超出 preflight 批准范围')
        if not biases:
            result['status']='skipped_no_reliable_interval';save();return result
        if inspection.get('bit')!=bit:raise ValueError('qobj bit does not match selected S21ZPA file')
        freq=np.arange(settings.spec_start,settings.spec_stop,settings.spec_step)
        for bias in biases:
            attempt=dict(bias_z=bias,status='running');result['attempts'].append(attempt);save()
            event('set_bias','started',bias_z=bias)
            # Required assignment. apply_bias must apply/ramp actual hardware,
            # wait until stable and verify actual readback before RF experiments.
            if dry:
                readback=None      # 干跑不写寄存器
            else:
                readback=adapter.apply_bias(ctx,bias)
            event('set_bias','completed',bias_z=bias,readback=readback)
            attempt['bias_readback']=readback
            # 三个实验合成一次下发（.qwf 路线）或一次顺序调用（direct 路线）
            steps=[step('s1','sq.spectroscopy',
                        {'freq':{'$arange':[settings.spec_start,settings.spec_stop,settings.spec_step]},
                         'spec_amp':settings.spec_amp,'update':settings.search_update},
                        timeout=2400,note=f'{bit} bias_z={bias} spectroscopy'),
                   step('q1','sq.iqraw',{'update':settings.search_update},
                        timeout=300,note=f'{bit} bias_z={bias} IQraw'),
                   step('p1','sq.piamp',{'amp':settings.piamp_amp,'update':settings.search_update},
                        timeout=600,note=f'{bit} bias_z={bias} PiPulse')]
            event('bias_probe','started',bias_z=bias)
            probe=ctx.runner.batch(steps,name=f'{bit}_probe_b{bias}'.replace('-','m').replace('.','p'),
                                   description=f'{bit} bias_z={bias} 探测（spectroscopy + iqraw + piamp）',
                                   max_wall='90m')
            event('bias_probe','completed',bias_z=bias,datasets=[p.get('dataset_id') for p in probe])
            spec,iq,pi=probe
            attempt['datasets']={'spectroscopy':spec.get('dataset_id'),'iqraw':iq.get('dataset_id'),'piamp':pi.get('dataset_id')}
            if spec.get('path'):attempt['spectroscopy_path']=spec['path']
            if iq.get('path'):attempt['iqraw_datavault_path']=iq['path']
            if not iq.get('path') and not dry:
                raise ValueError('Current IQraw DataVault result handle missing')
            if dry:
                attempt.update(status='dry_run');save();break
            # The real IQraw schema has stats, Is|I, Qs|I, Is|X, Qs|X;
            # visibility must be calculated from this current result handle.
            # Always perform piamp as requested, even if visibility is weak.
            visibility=metric_number(adapter.maximum_visibility(iq,ctx),'Maximum Visibility',0,1)
            excited=adapter.piamp_excited(pi,ctx)
            if not isinstance(excited,(bool,np.bool_)):raise ValueError('piamp_excited must return explicit bool')
            attempt.update(visibility=visibility,piamp_excited=bool(excited),status='accepted' if visibility>settings.visibility_threshold and excited else 'rejected')
            save()
            if attempt['status']=='accepted':result['selected_bias_z']=bias;break
        if result['selected_bias_z'] is None:
            if dry and biases:
                b=biases[0]   # 干跑：用占位 bias 把标定链也生成 + 校验一遍
            else:
                result['status']='no_working_bias';return result
        else:
            b=result['selected_bias_z']
        returns={}
        # 标定链：严格按旧版序列（pidf(df=0.08) → pidf → ramsey_df → s21_dis →
        # iqraw → set_pi(X) → set_pi(X/2) → t1 → ramsey_t2 → xeb → iqraw）
        chain=[
            step('d1','sq.pidf',{'df':settings.pidf_df},600,f'pidf df={settings.pidf_df} 定 f10'),
            step('d2','sq.pidf',{},600,'pidf 默认'),
            step('r1','sq.ramsey_df',{},900,'ramsey_df 精调 f10'),
            step('m1','sq.s21_dis',{},400,'s21_dis 读出区分度'),
            step('q1','sq.iqraw',{},300,'IQraw（pi 之前）'),
            step('x1','sq.set_pi',{'gate':'X'},300,'set_pi gate=X'),
            step('x2','sq.set_pi',{'gate':'X/2'},300,'set_pi gate=X/2'),
            step('t1','sq.t1',{'zpa':b,'delay':{'$arange':[0,settings.t1_delay_stop,settings.t1_delay_step]}},1800,f'T1（zpa={b}）'),
            step('e1','sq.ramsey_t2',{'delay':{'$arange':[0,settings.ramsey_t2_delay_stop,settings.ramsey_t2_delay_step]}},1800,'ramsey_t2（T2*）'),
            step('xb','sq.xeb',{'m':settings.xeb_m,'k':settings.xeb_k,'stats':settings.xeb_stats},3600,'xeb 随机基准'),
            step('q2','sq.iqraw',{},300,'IQraw（标定后）'),
        ]
        event('calibration_chain','started',bias_z=b)
        chain_results=ctx.runner.batch(chain,name=f'{bit}_chain',
                                       description=f'{bit} 标定链：bias_z={b}',
                                       max_wall='180m')
        event('calibration_chain','completed',bias_z=b,datasets=[c.get('dataset_id') for c in chain_results])
        for item in chain_results:
            returns[item['alias']]=item
            result['calibration'].append(dict(step=item['alias'],fn=item['fn'],args=item['args'],
                                              dataset=item.get('dataset_id'),path=item.get('path'),
                                              status='completed'))
            save()
        if dry:
            # 干跑：只验证 .qwf 能过 parse/validate，不取指标、不动寄存器
            result['status']='dry_run';return result
        # Adapter maps actual API outputs/fit parameters and explicitly converts
        # T1/T2 to us and f01 to GHz. Missing metrics are errors, never made up.
        current=float(adapter.read_bias(ctx))
        if not math.isclose(current,float(b),abs_tol=1e-9,rel_tol=0):raise ValueError('Calibration unexpectedly changed bias_z')
        metrics=adapter.final_metrics(ctx,returns)
        for key in ['readout_fidelity','T1_us','T2_us','qubit_frequency_GHz']:
            if key not in metrics:raise ValueError('Final metric missing '+key)
            metrics[key]=metric_number(metrics[key],key,0,1 if key=='readout_fidelity' else None)
            if key!='readout_fidelity' and metrics[key]<=0:raise ValueError('Nonpositive '+key)
        if not isinstance(metrics.get('T2_type'),str) or not metrics['T2_type']:raise ValueError('Must specify T2_type (e.g. Ramsey T2*)')
        result['metrics']=metrics;result['status']='success';success=True
        return result
    except BaseException as exc:
        result['status']='interrupted' if isinstance(exc,(KeyboardInterrupt,SystemExit)) else 'error'
        result['error']=dict(type=type(exc).__name__,message=str(exc),traceback=traceback.format_exc());save()
        if isinstance(exc,(KeyboardInterrupt,SystemExit)):raise
        return result
    finally:
        # Always attempt RF shutdown, then restore on failure. Log cleanup errors.
        cleanup=[]
        if not dry:
            try:adapter.safe_shutdown(ctx)
            except BaseException as e:cleanup.append('safe_shutdown: '+repr(e))
            if not success or cleanup:
                try:adapter.restore(ctx,snapshot)
                except BaseException as e:cleanup.append('restore: '+repr(e))
        if cleanup:result['cleanup_errors']=cleanup;result['status']='cleanup_failed'
        result['finished_at']=utcnow();save()


def write_summary(path,results):
    fields=['bit','source_id','status','selected_bias_z','readout_fidelity','T1_us','T2_us','T2_type','qubit_frequency_GHz']
    with Path(path).open('w',encoding='utf-8-sig',newline='') as fo:
        w=csv.DictWriter(fo,fieldnames=fields,extrasaction='ignore');w.writeheader()
        for r in results:w.writerow(dict(r,**(r.get('metrics') or {})))


def run_manifest(manifest,adapter=None,execute=False,dry=False):
    """One explicit current file per bit; no historical/latest-file guessing."""
    manifest_path=Path(manifest).resolve();cfg=json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    settings=Settings(**cfg.get('settings',{}));settings.validate()
    out=Path(cfg.get('out','zpa_pipeline_output'));out=out if out.is_absolute() else manifest_path.parent/out
    if out.exists() and any(out.iterdir()):raise ValueError('Output directory must be empty to prevent accidental rerun or overwriting a prior run')
    out.mkdir(exist_ok=True,parents=True);entries=cfg['qubits'];bits=[e['bit'] for e in entries]
    if len(bits)!=len(set(bits)):raise ValueError('One current source file per bit is required')
    model_path=Path(cfg.get('model','zpa_v2/model.json'));model_path=model_path if model_path.is_absolute() else manifest_path.parent/model_path
    model=load_model(model_path);inspections=[]
    # Prepare everything before any hardware call.
    for e in entries:
        source=Path(e['s21zpa_file']);source=source if source.is_absolute() else manifest_path.parent/source
        r=inspect_file(source,settings,model)
        if r['bit']!=e['bit']:raise ValueError('File/bit mismatch')
        inspections.append(r)
    save_json(out/'offline_plan.json',dict(created_at=utcnow(),settings=asdict(settings),entries=inspections,execution_enabled=execute,dry=dry))
    if not execute and not dry:return inspections
    if dry and settings.execution_route!='qwf':raise ValueError('--dry 只支持 qwf 路线（direct 路线会真的调实验）')
    if not cfg.get('datavault_root'):raise ValueError('Manifest requires datavault_root for live result discovery')
    if adapter is None:raise ValueError('Execution requires explicit adapter')
    results=[]
    for entry,inspection in zip(entries,inspections):
        # 真机执行且现场重采 S21ZPA 时，manifest 里那张历史文件只用于离线计划，
        # 不再决定能不能执行；区间以现场那张为准。
        manifest_required=not (execute and settings.acquire_s21zpa)
        if not inspection['pipeline_eligible'] and manifest_required:
            r=dict(bit=entry['bit'],source_id=inspection['id'],status='skipped_no_reliable_interval',metrics=None)
        else:
            try:
                ctx=build_context(adapter,entry['bit'],settings,out,dry=dry)
                r=run_bit(entry['bit'],ctx,adapter,inspection,out,settings,model,dry=dry)
            except BaseException as exc:
                checkpoint=out/(entry['bit']+'_result.json')
                r=json.loads(checkpoint.read_text(encoding='utf-8')) if checkpoint.exists() else dict(bit=entry['bit'],source_id=inspection['id'],status='interrupted' if isinstance(exc,(KeyboardInterrupt,SystemExit)) else 'error',metrics=None,error=str(exc))
                results.append(r);save_json(out/'results.json',results);write_summary(out/'summary.csv',results)
                if isinstance(exc,(KeyboardInterrupt,SystemExit)):raise
                break
        results.append(r);save_json(out/'results.json',results);write_summary(out/'summary.csv',results)
        # Never continue manipulating further bits after a failed cleanup.
        if r['status'] in ['cleanup_failed','error']:break
    return results


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('manifest',type=Path);ap.add_argument('--execute',action='store_true');ap.add_argument('--dry',action='store_true',help='生成 .qwf 并做 parse/validate，不下发实验（需要 :3008 在跑）');ap.add_argument('--adapter',help='Trusted Python module exporting create_adapter(config). Imports execute code.');a=ap.parse_args()
    if a.execute and not a.adapter:ap.error('--execute needs --adapter; use offline mode first')
    if a.dry and not a.adapter:ap.error('--dry needs --adapter (要拿当前比特/session)')
    if a.execute and a.dry:ap.error('--execute and --dry are mutually exclusive')
    adapter=None
    if a.execute or a.dry:
        cfg=json.loads(a.manifest.read_text(encoding='utf-8-sig'));adapter=importlib.import_module(a.adapter).create_adapter(cfg.get('adapter_config',{}))
    results=run_manifest(a.manifest,adapter,a.execute,a.dry)
    print(json.dumps(serializable([dict(bit=r['bit'],status=r.get('status','offline_planned'),bias_values=r.get('scan_bias_values'),metrics=r.get('metrics')) for r in results]),ensure_ascii=False,indent=2))
    if a.execute and any(r.get('status')!='success' for r in results):raise SystemExit(2)
if __name__=='__main__':main()

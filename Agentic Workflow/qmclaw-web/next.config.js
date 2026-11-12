/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  experimental: {
    // recharts 的包处理方式（Next 14.2.x）。
    //
    // 不要写成 `experimental.serverComponentsExternalPackages: ['recharts']`：
    // Next 14.2 的 `optimizePackageImports` 默认列表里已经含 'recharts'，
    // 而 optimizePackageImports 的条目会被并入 transpilePackages，
    // 与 serverComponentsExternalPackages 撞车后 next build 直接报
    // "The packages specified in the 'transpilePackages' conflict with the
    //  'serverComponentsExternalPackages': recharts"。
    //
    // 也不要写成顶层 `serverExternalPackages`（那是 Next 15 的键名）：本仓库
    // 锁定 next 14.2.35，该键不被识别，只会打一条
    // "Unrecognized key(s) in object: 'serverExternalPackages'" 告警，
    // 且达不到"服务端不打包 recharts"的原意。
    //
    // 显式声明 optimizePackageImports 才是 Next 14 下的正确写法：它既保留了
    // 对 recharts 的按需导入优化（与 Next 14 默认行为一致），又不会与
    // transpilePackages 冲突。
    optimizePackageImports: ['recharts'],
  },
};

module.exports = nextConfig;

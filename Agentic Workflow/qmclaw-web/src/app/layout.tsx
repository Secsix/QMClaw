import type { Metadata } from "next";
import "@/styles/variables.css";
import "@/styles/animations.css";
import "@/styles/responsive.css";
import "@/theme/tokens.css";
import ThemeProvider from "@/theme/ThemeProvider";

export const metadata: Metadata = {
  title: "qmclaw - Quantum Measurement & Calibration Workflow",
  description: "Intelligent quantum measurement and calibration control system",
};

// 首屏之前就写好主题，避免深浅色切换时闪白
const THEME_BOOTSTRAP = `(function(){try{var t=localStorage.getItem("qmclaw-theme");if(t!=="light"&&t!=="dark"){t="dark";}document.documentElement.dataset.qmTheme=t;}catch(e){document.documentElement.dataset.qmTheme="dark";}})();`;

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" data-qm-theme="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOTSTRAP }} />
      </head>
      <body style={{ margin: 0, padding: 0 }}>
        <ThemeProvider>{children}</ThemeProvider>
      </body>
    </html>
  );
}

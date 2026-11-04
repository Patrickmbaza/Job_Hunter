import type { Metadata } from "next";
import "./styles.css";

export const metadata: Metadata = {
  title: "Job Hunter — Personal workspace",
  description: "A private, local-first job search and application workspace.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}

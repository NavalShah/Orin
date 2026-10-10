import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Shell } from "@/components/shell/Shell";
import "./tokens.css";

export const metadata: Metadata = {
  title: "Orin",
  description: "Hear music, see it written in different traditions, and see what each notation leaves out.",
};

// The shared shell for /chat, /studio and /workspace: navigation, session gate, status strip,
// Library and glossary. Screens read shared state with the hooks in components/shell.
export default function OrinLayout({ children }: { children: ReactNode }) {
  return (
    <div className="orin">
      <Shell>{children}</Shell>
    </div>
  );
}

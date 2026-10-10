import { Suspense } from "react";
import Workspace from "@/components/workspace/Workspace";

export const metadata = { title: "Workspace - Orin" };

// useSearchParams (project and handoff links) needs a Suspense boundary.
export default function WorkspacePage() {
  return (
    <Suspense fallback={<p className="o-muted">Loading the workspace...</p>}>
      <Workspace />
    </Suspense>
  );
}

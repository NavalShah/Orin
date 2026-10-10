import type { Metadata } from "next";
import { Suspense } from "react";
import { StudioScreen } from "@/components/studio/StudioScreen";

export const metadata: Metadata = { title: "Notation Studio - Orin" };

// useSearchParams (the ?asset= and ?conversion= handoffs) needs a Suspense boundary.
export default function StudioPage() {
  return (
    <Suspense fallback={<p role="status">Loading the Notation Studio...</p>}>
      <StudioScreen />
    </Suspense>
  );
}

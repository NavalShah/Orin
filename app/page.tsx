import type { Metadata } from "next";
import { Home } from "@/components/home/Home";

export const metadata: Metadata = {
  title: "Orin: one melody, two ways to hear it",
  description: "Orin writes music from one tradition in the notation of another and shows what the new notation could not carry.",
};

export default function Page() {
  return <Home />;
}

import type { Metadata } from "next";
import { Suspense } from "react";
import { ChatScreen } from "@/components/chat/ChatScreen";

export const metadata: Metadata = { title: "Chat - Orin" };

// useSearchParams (the ?conversion= and ?asset= handoffs) needs a Suspense boundary.
export default function ChatPage() {
  return (
    <Suspense fallback={<p role="status">Loading the chat...</p>}>
      <ChatScreen />
    </Suspense>
  );
}

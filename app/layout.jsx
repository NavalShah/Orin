import "./globals.css";

export const metadata = {
  title: "Orin",
  description: "Convert music between traditions and see what each notation leaves out.",
};

export default function RootLayout({ children }) {
  return <html lang="en"><body>{children}</body></html>;
}

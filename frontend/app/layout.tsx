import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import { BackendStatus } from "@/components/backend-status";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
	title: "Support Triage Agent",
	description:
		"A tool-using LLM agent that investigates support tickets and proposes actions for a human to approve.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
	return (
		<html lang="en" className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}>
			<body className="flex min-h-full flex-col font-sans">
				<header className="border-b border-border bg-surface">
					<div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3">
						<Link href="/" className="flex items-baseline gap-2">
							<span className="font-semibold">Lumora Support</span>
							<span className="hidden text-sm text-muted sm:inline">triage agent demo</span>
						</Link>
						<nav className="flex items-center gap-4 text-sm">
							<BackendStatus />
							<Link href="/new" className="rounded-md bg-accent px-3 py-1.5 font-medium text-white">
								New ticket
							</Link>
						</nav>
					</div>
				</header>
				<main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6">{children}</main>
				<footer className="border-t border-border px-4 py-4 text-center text-xs text-muted">
					Fictional company and data. Every agent action is a proposal until a human approves it.
				</footer>
			</body>
		</html>
	);
}

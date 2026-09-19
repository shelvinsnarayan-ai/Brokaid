export function KKbd({ children, testId }: { children: React.ReactNode; testId?: string }) {
  return <kbd className="k-kbd" data-testid={testId}>{children}</kbd>;
}

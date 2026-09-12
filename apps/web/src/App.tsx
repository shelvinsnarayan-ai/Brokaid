import { lazy, Suspense } from "react";
import { Route, Routes } from "react-router-dom";
import { Landing } from "./pages/Landing";
import { ClientShell } from "./shells/ClientShell";
import { OperatorShell } from "./shells/OperatorShell";
import { OperatorMatters } from "./pages/OperatorMatters";
import { DevAuthBanner } from "./components/DevAuthBanner";

const DevLogin = lazy(() =>
  import("./pages/DevLogin").then((m) => ({ default: m.DevLogin }))
);

export function App() {
  return (
    <>
      {__AUTH_DEV_MODE__ ? <DevAuthBanner /> : null}
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/c/*" element={<ClientShell />} />
        <Route path="/o" element={<OperatorShell />} />
        <Route path="/o/matters" element={<OperatorMatters />} />
        {__AUTH_DEV_MODE__ ? (
          <Route
            path="/dev/login"
            element={
              <Suspense fallback={null}>
                <DevLogin />
              </Suspense>
            }
          />
        ) : null}
      </Routes>
    </>
  );
}

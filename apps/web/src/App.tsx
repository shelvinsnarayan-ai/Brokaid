import { lazy, Suspense } from "react";
import { Route, Routes } from "react-router-dom";
import { Landing } from "./pages/Landing";
import { ClientHome } from "./pages/ClientHome";
import { OperatorShell } from "./shells/OperatorShell";
import { OperatorMatters } from "./pages/OperatorMatters";
import { OperatorPolicies } from "./pages/OperatorPolicies";
import { ClientLogin } from "./pages/ClientLogin";
import { OperatorLogin } from "./pages/OperatorLogin";
import { OperatorEnrol } from "./pages/OperatorEnrol";
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
        <Route path="/c" element={<ClientHome />} />
        <Route path="/c/login" element={<ClientLogin />} />
        <Route path="/o" element={<OperatorShell />} />
        <Route path="/o/login" element={<OperatorLogin />} />
        <Route path="/o/enrol" element={<OperatorEnrol />} />
        <Route path="/o/matters" element={<OperatorMatters />} />
        <Route path="/o/policies" element={<OperatorPolicies />} />
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

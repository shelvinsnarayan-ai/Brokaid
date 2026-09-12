import { lazy, Suspense } from "react";
import { Route, Routes } from "react-router-dom";
import { Landing } from "./pages/Landing";
import { ClientLayout } from "./shells/ClientLayout";
import { ClientHome } from "./pages/ClientHome";
import { ClientCover } from "./pages/ClientCover";
import { ClientMoney } from "./pages/ClientMoney";
import { ClientClaims, ClaimsNew } from "./pages/ClientClaims";
import { ClientAsk } from "./pages/ClientAsk";
import { ClientProfile } from "./pages/ClientProfile";
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
const KitchenSink = lazy(() =>
  import("./pages/KitchenSink").then((m) => ({ default: m.KitchenSink }))
);

export function App() {
  return (
    <>
      {__AUTH_DEV_MODE__ ? <DevAuthBanner /> : null}
      <Routes>
        <Route path="/" element={<Landing />} />

        <Route path="/c/login" element={<ClientLogin />} />
        <Route path="/c" element={<ClientLayout />}>
          <Route index element={<ClientHome />} />
          <Route path="cover" element={<ClientCover />} />
          <Route path="money" element={<ClientMoney />} />
          <Route path="claims" element={<ClientClaims />} />
          <Route path="claims/new" element={<ClaimsNew />} />
          <Route path="ask" element={<ClientAsk />} />
          <Route path="profile" element={<ClientProfile />} />
          {__AUTH_DEV_MODE__ ? (
            <Route
              path="_kitchen-sink"
              element={
                <Suspense fallback={null}>
                  <KitchenSink />
                </Suspense>
              }
            />
          ) : null}
        </Route>

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

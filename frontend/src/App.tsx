import { lazy, Suspense } from "react";
import { Route, Routes } from "react-router-dom";
import { Landing } from "./pages/Landing";
import { ClientLayout } from "./shells/ClientLayout";
import { ClientHome } from "./pages/ClientHome";
import { ClientCover } from "./pages/ClientCover";
import { ClientPendingQuote } from "./pages/ClientPendingQuote";
import { ClientRequestChange } from "./pages/ClientRequestChange";
import { ClientPay } from "./pages/ClientPay";
import { ClientSign } from "./pages/ClientSign";
import { ClientCertificate } from "./pages/ClientCertificate";
import { VerifyPublic } from "./pages/VerifyPublic";
import { ClientMoney } from "./pages/ClientMoney";
import { ClientClaims, ClaimsNew, ClaimDetail } from "./pages/ClientClaims";
import { ClientNotifications } from "./pages/ClientNotifications";
import { ClientAsk } from "./pages/ClientAsk";
import { ClientProfile } from "./pages/ClientProfile";
import { OperatorControlLayout } from "./shells/OperatorControlLayout";
import { Dashboard as OperatorDashboard } from "./pages/operator/Dashboard";
import { Queue as OperatorQueue } from "./pages/operator/Queue";
import { DecisionCard } from "./pages/operator/DecisionCard";
import { DailyBrief } from "./pages/operator/DailyBrief";
import { KpiDetail } from "./pages/operator/KpiDetail";
import { Clients as OperatorClients } from "./pages/operator/Clients";
import { Client360 } from "./pages/operator/Client360";
import { OperatorMatters } from "./pages/OperatorMatters";
import { OperatorPolicies } from "./pages/OperatorPolicies";
import { OperatorClaim } from "./pages/operator/OperatorClaim";
import { ShadowMode, ShadowModeDrill } from "./pages/operator/ShadowMode";
import { TrailExport } from "./pages/operator/TrailExport";
import { ChaseLadder } from "./pages/operator/ChaseLadder";
import "./styles/shadow-mode.css";
import "./styles/trail-export.css";
import "./styles/chase-ladder.css";
import { ClientLogin } from "./pages/ClientLogin";
import { OperatorLogin } from "./pages/OperatorLogin";
import { OperatorEnrol } from "./pages/OperatorEnrol";
import { Login } from "./pages/Login";
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
        <Route path="/login" element={<Login />} />
        <Route path="/verify/:token" element={<VerifyPublic />} />

        <Route path="/c/login" element={<ClientLogin />} />
        <Route path="/c" element={<ClientLayout />}>
          <Route index element={<ClientHome />} />
          <Route path="cover" element={<ClientCover />} />
          <Route path="cover/:policy_id/pending" element={<ClientPendingQuote />} />
          <Route path="cover/:policy_id/request-change" element={<ClientRequestChange />} />
          <Route path="cover/:policy_id/pay/:envelope_id" element={<ClientPay />} />
          <Route path="cover/:policy_id/sign/:envelope_id" element={<ClientSign />} />
          <Route path="cover/:policy_id/certificate/:envelope_id" element={<ClientCertificate />} />
          <Route path="cover/:policy_id/certificate" element={<ClientCertificate />} />
          <Route path="money" element={<ClientMoney />} />
          <Route path="claims" element={<ClientClaims />} />
          <Route path="claims/new" element={<ClaimsNew />} />
          <Route path="claims/:claim_id" element={<ClaimDetail />} />
          <Route path="notifications" element={<ClientNotifications />} />
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

        <Route path="/o/login" element={<OperatorLogin />} />
        <Route path="/o/enrol" element={<OperatorEnrol />} />
        <Route path="/o" element={<OperatorControlLayout />}>
          <Route index element={<OperatorDashboard />} />
          <Route path="queue" element={<OperatorQueue />} />
          <Route path="queue/:envelope_id" element={<DecisionCard />} />
          <Route path="brief" element={<DailyBrief />} />
          <Route path="kpi/:id" element={<KpiDetail />} />
          <Route path="clients" element={<OperatorClients />} />
          <Route path="clients/:id" element={<Client360 />} />
          <Route path="matters" element={<OperatorMatters />} />
          <Route path="policies" element={<OperatorPolicies />} />
          <Route path="claims/:claim_id" element={<OperatorClaim />} />
          <Route path="shadow-mode" element={<ShadowMode />} />
          <Route path="shadow-mode/:agent/:class_key" element={<ShadowModeDrill />} />
          <Route path="export" element={<TrailExport />} />
          <Route path="chase" element={<ChaseLadder />} />
        </Route>

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

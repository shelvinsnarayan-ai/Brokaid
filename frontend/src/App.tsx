import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { Landing } from "./pages/Landing";
import { ClientLayout } from "./shells/ClientLayout";
import { ClientHome } from "./pages/ClientHome";
import { ClientCover } from "./pages/ClientCover";
import { ClientPendingQuote } from "./pages/ClientPendingQuote";
import { ClientRequestChange } from "./pages/ClientRequestChange";
import { ClientPay } from "./pages/ClientPay";
import { ClientSign } from "./pages/ClientSign";
import { ClientCertificate } from "./pages/ClientCertificate";
import { ClientCertificateNew } from "./pages/ClientCertificateNew";
import { VerifyPublic } from "./pages/VerifyPublic";
import "./styles/cert-phase2.css";
import { ClientMoney } from "./pages/ClientMoney";
import { ClientRenewal } from "./pages/ClientRenewal";
import { ClientComplaint, ClientDocuments, ClientMyData } from "./pages/ClientCompliance";
import { CompliancePage } from "./pages/operator/Compliance";
import { ClientClaims, ClaimsNew, ClaimDetail } from "./pages/ClientClaims";
import { ClientNotifications } from "./pages/ClientNotifications";
import { ClientAsk } from "./pages/ClientAsk";
import { ClientProfile } from "./pages/ClientProfile";
import { OperatorControlLayout } from "./shells/OperatorControlLayout";
import { Dashboard as OperatorDashboard } from "./pages/operator/Dashboard";
import { Queue as OperatorQueue } from "./pages/operator/Queue";
import { DecisionCard } from "./pages/operator/DecisionCard";
import { DailyBrief } from "./pages/operator/DailyBrief";
import { WeeklyPack } from "./pages/operator/WeeklyPack";
import { Renewals } from "./pages/operator/Renewals";
import { Inbox } from "./pages/operator/Inbox";
import { AlertsPage } from "./components/alerts/AlertsPage";
import { InboxThread } from "./pages/operator/InboxThread";
import { KpiDetail } from "./pages/operator/KpiDetail";
import { Clients as OperatorClients } from "./pages/operator/Clients";
import { Client360 } from "./pages/operator/Client360";
import { OperatorMatters } from "./pages/OperatorMatters";
import { OperatorPolicies } from "./pages/OperatorPolicies";
import { OperatorClaim } from "./pages/operator/claims/Detail";
import ClaimsPage from "./pages/operator/Claims";
import { ShadowMode, ShadowModeDrill } from "./pages/operator/ShadowMode";
import { Governance } from "./pages/operator/Governance";
import Platform from "./pages/platform/Platform";
import { TrailExport } from "./pages/operator/TrailExport";
import { ChaseLadder } from "./pages/operator/ChaseLadder";
import "./styles/shadow-mode.css";
import "./styles/trail-export.css";
import "./styles/chase-ladder.css";
import { ClientLogin } from "./pages/ClientLogin";
import { OperatorLogin } from "./pages/OperatorLogin";
import { OperatorEnrol } from "./pages/OperatorEnrol";
import { Login } from "./pages/Login";
import SignIn from "./pages/auth/SignIn";
import AcceptInvite from "./pages/auth/AcceptInvite";
import ResetPassword from "./pages/auth/ResetPassword";
import AccountSecurity from "./pages/auth/AccountSecurity";
import { Invites as OperatorInvites } from "./pages/operator/Invites";
import { Users as OperatorUsers } from "./pages/operator/Users";
import { ImportPage } from "./pages/operator/Import";
import { BankPage } from "./pages/operator/Bank";
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
      {import.meta.env.BROKAID_AUTH_DEV_MODE ? <DevAuthBanner /> : null}
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/login" element={<SignIn />} />
        <Route path="/platform/*" element={<Platform />} />
        <Route path="/invite/:token" element={<AcceptInvite />} />
        <Route path="/reset/:token" element={<ResetPassword />} />
        <Route path="/account/security" element={<AccountSecurity />} />
        {import.meta.env.BROKAID_AUTH_DEV_MODE ? <Route path="/demo" element={<Login />} /> : null}
        <Route path="/verify" element={<VerifyPublic />} />
        <Route path="/verify/:token" element={<VerifyPublic />} />

        <Route path="/c/login" element={import.meta.env.BROKAID_AUTH_DEV_MODE ? <ClientLogin /> : <Navigate to="/login" replace />} />
        <Route path="/c" element={<ClientLayout />}>
          <Route index element={<ClientHome />} />
          <Route path="cover" element={<ClientCover />} />
          <Route path="cover/:policy_id/pending" element={<ClientPendingQuote />} />
          <Route path="cover/:policy_id/request-change" element={<ClientRequestChange />} />
          <Route path="cover/:policy_id/pay/:envelope_id" element={<ClientPay />} />
          <Route path="cover/:policy_id/sign/:envelope_id" element={<ClientSign />} />
          <Route path="cover/:policy_id/certificate/new" element={<ClientCertificateNew />} />
          <Route path="cover/:policy_id/certificate/:envelope_id" element={<ClientCertificate />} />
          <Route path="cover/:policy_id/certificate" element={<ClientCertificate />} />
          <Route path="money" element={<ClientMoney />} />
          <Route path="renewal/:renewal_id" element={<ClientRenewal />} />
          <Route path="documents" element={<ClientDocuments />} />
          <Route path="complaint" element={<ClientComplaint />} />
          <Route path="my-data" element={<ClientMyData />} />
          <Route path="claims" element={<ClientClaims />} />
          <Route path="claims/new" element={<ClaimsNew />} />
          <Route path="claims/:claim_id" element={<ClaimDetail />} />
          <Route path="notifications" element={<ClientNotifications />} />
          <Route path="ask" element={<ClientAsk />} />
          <Route path="alerts" element={<AlertsPage surface="client" />} />
          <Route path="profile" element={<ClientProfile />} />
          {import.meta.env.BROKAID_AUTH_DEV_MODE ? (
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

        <Route path="/o/login" element={import.meta.env.BROKAID_AUTH_DEV_MODE ? <OperatorLogin /> : <Navigate to="/login" replace />} />
        <Route path="/o/enrol" element={<OperatorEnrol />} />
        <Route path="/o" element={<OperatorControlLayout />}>
          <Route index element={<OperatorDashboard />} />
          <Route path="queue" element={<OperatorQueue />} />
          <Route path="queue/:envelope_id" element={<DecisionCard />} />
          <Route path="brief" element={<DailyBrief />} />
          <Route path="weekly-pack" element={<WeeklyPack />} />
          <Route path="renewals" element={<Renewals />} />
          <Route path="inbox" element={<Inbox />} />
          <Route path="alerts" element={<AlertsPage surface="operator" />} />
          <Route path="inbox/:thread_id" element={<InboxThread />} />
          <Route path="kpi/:id" element={<KpiDetail />} />
          <Route path="clients" element={<OperatorClients />} />
          <Route path="clients/:id" element={<Client360 />} />
          <Route path="matters" element={<OperatorMatters />} />
          <Route path="policies" element={<OperatorPolicies />} />
          <Route path="claims" element={<ClaimsPage />} />
          <Route path="claims/:claim_id" element={<OperatorClaim />} />
          <Route path="shadow-mode" element={<ShadowMode />} />
          <Route path="governance" element={<Governance />} />
          <Route path="invites" element={<OperatorInvites />} />
          <Route path="users" element={<OperatorUsers />} />
          <Route path="import" element={<ImportPage />} />
          <Route path="bank" element={<BankPage />} />
          <Route path="compliance" element={<CompliancePage />} />
          <Route path="shadow-mode/:agent/:class_key" element={<ShadowModeDrill />} />
          <Route path="export" element={<TrailExport />} />
          <Route path="chase" element={<ChaseLadder />} />
        </Route>

        {import.meta.env.BROKAID_AUTH_DEV_MODE ? (
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

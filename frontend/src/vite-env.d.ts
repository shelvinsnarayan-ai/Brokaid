/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly BROKAID_AUTH_DEV_MODE: boolean;
  readonly BROKAID_DEMO_SIGNIN: boolean;
  readonly BROKAID_BUILD_SHA: string;
  readonly BROKAID_BUILD_SIGNED: string;
}

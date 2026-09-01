interface Window {
    __subscribeBridgeFetchPatched?: boolean;
    bridgeLogin?: () => Promise<void>;
}

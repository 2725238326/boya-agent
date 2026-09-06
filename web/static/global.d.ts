interface Window {
    __subscribeBridgeFetchPatched?: boolean;
    __persistentLoginFetchPatched?: boolean;
    bridgeLogin?: () => Promise<void>;
    __portalPersistentLogin?: {
        save: (email: string) => void;
        clear: () => void;
    };
}

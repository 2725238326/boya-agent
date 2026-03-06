"use strict";

(function () {
    const STORAGE_TICKET_KEY = "portal_bridge_ticket";
    const STORAGE_EXPIRES_KEY = "portal_bridge_expires_at";
    const DEFAULT_TTL_SECONDS = 15 * 60;

    let bridgeTicket = "";
    let bridgePollTimer = null;

    function getMsgBox() {
        return document.getElementById("msgBox");
    }

    function showMessage(text, type) {
        const msg = getMsgBox();
        if (!msg) return;
        msg.className = type === "error" ? "message error" : "message success";
        msg.textContent = text;
        msg.style.display = "block";
    }

    function ensureBridgeButton() {
        const loginBtn = document.getElementById("loginBtn");
        if (!loginBtn || document.getElementById("bridgeLoginBtn")) return;

        const btn = document.createElement("button");
        btn.type = "button";
        btn.id = "bridgeLoginBtn";
        btn.className = "btn";
        btn.style.display = "none";
        btn.textContent = "\u6211\u5df2\u5728\u5176\u4ed6\u8bbe\u5907\u5b8c\u6210\u9a8c\u8bc1\uff0c\u70b9\u51fb\u8fd9\u91cc\u767b\u5f55";
        btn.addEventListener("click", bridgeLogin);

        loginBtn.insertAdjacentElement("afterend", btn);
    }

    function setBridgeButtonVisible(visible) {
        const btn = document.getElementById("bridgeLoginBtn");
        if (!btn) return;
        btn.style.display = visible ? "block" : "none";
    }

    function persistBridgeTicket(ticket, expiresIn) {
        if (!ticket) return;
        bridgeTicket = ticket;
        const ttl = Number(expiresIn || 0) || DEFAULT_TTL_SECONDS;
        const expiresAt = Date.now() + ttl * 1000;
        sessionStorage.setItem(STORAGE_TICKET_KEY, bridgeTicket);
        sessionStorage.setItem(STORAGE_EXPIRES_KEY, String(expiresAt));
    }

    function clearBridgeTicket() {
        bridgeTicket = "";
        sessionStorage.removeItem(STORAGE_TICKET_KEY);
        sessionStorage.removeItem(STORAGE_EXPIRES_KEY);
    }

    function loadBridgeTicket() {
        const ticket = (sessionStorage.getItem(STORAGE_TICKET_KEY) || "").trim();
        if (!ticket) return "";
        const expiresAt = Number(sessionStorage.getItem(STORAGE_EXPIRES_KEY) || "0");
        if (expiresAt && Date.now() > expiresAt) {
            clearBridgeTicket();
            return "";
        }
        bridgeTicket = ticket;
        return bridgeTicket;
    }

    function stopBridgePolling() {
        if (bridgePollTimer) {
            clearInterval(bridgePollTimer);
            bridgePollTimer = null;
        }
    }

    async function pollBridgeStatus() {
        if (!bridgeTicket) return;
        try {
            const resp = await fetch(`/api/subscribe/bridge/${encodeURIComponent(bridgeTicket)}/status`);
            const data = await resp.json();
            if (!data.success) return;

            const status = data.data || {};
            if (status.expired) {
                stopBridgePolling();
                clearBridgeTicket();
                setBridgeButtonVisible(false);
                showMessage("\u672c\u6b21\u9a8c\u8bc1\u72b6\u6001\u5df2\u8fc7\u671f\uff0c\u8bf7\u91cd\u65b0\u53d1\u9001\u767b\u5f55\u94fe\u63a5\u3002", "error");
                return;
            }
            if (status.verified) {
                stopBridgePolling();
                setBridgeButtonVisible(true);
                showMessage("\u5df2\u68c0\u6d4b\u5230\u4f60\u5b8c\u6210\u4e86\u90ae\u7bb1\u9a8c\u8bc1\uff0c\u73b0\u5728\u53ef\u4ee5\u76f4\u63a5\u767b\u5f55\uff0c\u65e0\u9700\u91cd\u65b0\u8f93\u5165\u90ae\u7bb1\u3002", "success");
            }
        } catch (_) {
            // next polling tick will retry
        }
    }

    function startBridgePolling(ticket, expiresIn) {
        if (!ticket) return;
        persistBridgeTicket(ticket, expiresIn);
        setBridgeButtonVisible(false);
        stopBridgePolling();
        pollBridgeStatus();
        bridgePollTimer = setInterval(pollBridgeStatus, 2000);
    }

    async function bridgeLogin() {
        if (!bridgeTicket) return;
        const btn = document.getElementById("bridgeLoginBtn");
        if (!btn) return;

        btn.disabled = true;
        btn.textContent = "\u767b\u5f55\u4e2d...";
        try {
            const resp = await fetch(`/api/subscribe/bridge/${encodeURIComponent(bridgeTicket)}/claim`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
            });
            const data = await resp.json();
            if (!data.success) {
                showMessage(data.error || "\u767b\u5f55\u5931\u8d25\uff0c\u8bf7\u91cd\u65b0\u53d1\u9001\u767b\u5f55\u94fe\u63a5\u3002", "error");
                return;
            }

            clearBridgeTicket();
            stopBridgePolling();
            const url = data.data && data.data.portal_url ? data.data.portal_url : "/portal?login=ok";
            window.location.href = url;
        } catch (_) {
            showMessage("\u7f51\u7edc\u5f02\u5e38\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5\u3002", "error");
        } finally {
            btn.disabled = false;
            btn.textContent = "\u6211\u5df2\u5728\u5176\u4ed6\u8bbe\u5907\u5b8c\u6210\u9a8c\u8bc1\uff0c\u70b9\u51fb\u8fd9\u91cc\u767b\u5f55";
        }
    }

    function pathOf(input) {
        try {
            if (typeof input === "string") return new URL(input, window.location.origin).pathname;
            if (input && typeof input.url === "string") return new URL(input.url, window.location.origin).pathname;
        } catch (_) {
            // ignore
        }
        return "";
    }

    function installFetchInterceptor() {
        if (window.__subscribeBridgeFetchPatched) return;
        window.__subscribeBridgeFetchPatched = true;

        const rawFetch = window.fetch.bind(window);
        window.fetch = async function (input, init) {
            const resp = await rawFetch(input, init);

            try {
                const path = pathOf(input);
                const method = ((init && init.method) || "GET").toUpperCase();
                if (
                    method === "POST" &&
                    (path === "/api/subscribe" || path === "/api/login/request")
                ) {
                    const data = await resp.clone().json().catch(() => null);
                    if (data && data.success && data.bridge_ticket) {
                        startBridgePolling(data.bridge_ticket, data.bridge_expires_in || DEFAULT_TTL_SECONDS);
                    }
                }
            } catch (_) {
                // no-op
            }

            return resp;
        };
    }

    document.addEventListener("DOMContentLoaded", function () {
        ensureBridgeButton();
        installFetchInterceptor();

        const restored = loadBridgeTicket();
        if (restored) {
            startBridgePolling(restored, 0);
        }
    });

    window.bridgeLogin = bridgeLogin;
})();

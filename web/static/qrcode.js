const qrcodePageState = {
    courseId: "",
};

function qrcodeStatusBox() {
    return document.getElementById("qrcodeStatusBox");
}

function setQrcodeStatus(message, type = "") {
    const box = qrcodeStatusBox();
    if (!box) return;
    box.className = `qrcode-status-box${type ? ` ${type}` : ""}`;
    box.textContent = message;
}

function buildQrcodeApiUrl(path) {
    const url = new URL(path, window.location.origin);
    if (qrcodePageState.courseId) {
        url.searchParams.set("course_id", qrcodePageState.courseId);
    }
    return `${url.pathname}${url.search}`;
}

async function qrcodeApi(url, options = {}) {
    const { headers = {}, ...fetchOptions } = options;
    try {
        const resp = await fetch(url, {
            headers: {
                Accept: "application/json",
                ...headers,
            },
            ...fetchOptions,
        });
        const contentType = (resp.headers.get("content-type") || "").toLowerCase();
        if (!contentType.includes("application/json")) {
            const text = await resp.text();
            return {
                success: false,
                error: `接口返回了非 JSON 响应 (${resp.status})`,
                bodyPreview: text.slice(0, 120),
            };
        }
        const data = await resp.json();
        if (!resp.ok && data && typeof data === "object") {
            data.success = false;
            if (!data.error) data.error = `请求失败 (${resp.status})`;
        }
        return data;
    } catch (err) {
        return { success: false, error: err.message || "网络请求失败" };
    }
}

function escapeHtml(text) {
    const map = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" };
    return String(text || "").replace(/[&<>"']/g, (char) => map[char]);
}

function updateContributorStats(data = {}) {
    const emailEl = document.getElementById("qrcodeCurrentUser");
    const countEl = document.getElementById("qrcodeUploadCount");
    const nextRewardEl = document.getElementById("qrcodeNextReward");
    if (!emailEl || !countEl || !nextRewardEl) return;

    const email = data.email || "";
    const stats = data.stats || {};
    emailEl.textContent = email || "未登录";
    countEl.textContent = String(stats.total_uploads || 0);
    nextRewardEl.textContent = stats.next_reward_threshold ? `${stats.next_reward_threshold} 次` : "已达到最高档";

    const emailInput = document.getElementById("qrcodeEmail");
    if (email && emailInput && !emailInput.value) {
        emailInput.value = email;
    }
}

function renderLeaderboard(containerId, leaderboard, emptyText) {
    const container = document.getElementById(containerId);
    if (!container) return;

    const items = leaderboard?.items || [];
    if (!items.length) {
        container.innerHTML = `<div class="qrcode-empty compact">${escapeHtml(emptyText)}</div>`;
        return;
    }

    container.innerHTML = items.map((item) => `
        <div class="qrcode-rank-item">
            <div class="qrcode-rank-left">
                <span class="qrcode-rank-index">#${item.rank}</span>
                <span class="qrcode-rank-name">${escapeHtml(item.masked_email || item.email || "匿名用户")}</span>
            </div>
            <span class="qrcode-rank-score">${Number(item.upload_count || 0)} 次</span>
        </div>
    `).join("");
}

function updateLeaderboardTitles(currentBoard) {
    const titleEl = document.getElementById("qrcodeCurrentLeaderboardTitle");
    if (!titleEl) return;
    titleEl.textContent = currentBoard?.period_label
        ? `本期贡献榜 · ${currentBoard.period_label}`
        : "本期贡献榜";
}

function renderUploads(items) {
    const container = document.getElementById("qrcodeList");
    if (!container) return;

    if (!items || !items.length) {
        container.innerHTML = '<div class="qrcode-empty">暂无共享二维码。你可以成为第一个上传的人。</div>';
        return;
    }

    container.innerHTML = items.map((item) => `
        <article class="qrcode-card">
            <img src="${escapeHtml(item.image_url)}" alt="${escapeHtml(item.course_name)}">
            <div class="qrcode-card-title">${escapeHtml(item.course_name)}</div>
            <div class="qrcode-card-meta">
                <div>${escapeHtml(item.course_time || "时间待补充")}</div>
                <div>${escapeHtml(item.course_location || "地点待补充")}</div>
            </div>
            <div class="qrcode-card-notes">${escapeHtml(item.notes || "暂无补充说明")}</div>
            <div class="qrcode-card-footer">
                <span>${escapeHtml(item.masked_contributor_email || item.contributor_email || "匿名用户")}</span>
                <span>累计 ${Number(item.contributor_upload_count || 0)} 次</span>
            </div>
        </article>
    `).join("");
}

async function loadQrcodeContext() {
    const result = await qrcodeApi(buildQrcodeApiUrl("/api/qrcode/context"));
    if (!result.success) {
        setQrcodeStatus(result.error || "无法加载当前贡献信息", "error");
        return;
    }

    const data = result.data || {};
    updateContributorStats(data);
    updateLeaderboardTitles(data.leaderboard_current);
    renderLeaderboard("qrcodeCurrentLeaderboard", data.leaderboard_current, "本期暂无贡献记录");
    renderLeaderboard("qrcodeAllTimeLeaderboard", data.leaderboard_all_time, "累计暂无贡献记录");
}

async function loadQrcodeUploads() {
    const result = await qrcodeApi(buildQrcodeApiUrl("/api/qrcode/uploads"));
    if (!result.success) {
        renderUploads([]);
        setQrcodeStatus(result.error || "二维码列表加载失败", "error");
        return;
    }
    renderUploads(result.data || []);
}

async function submitQrcodeForm(event) {
    event.preventDefault();

    const form = event.currentTarget;
    const submitButton = document.getElementById("qrcodeSubmitButton");
    if (submitButton) {
        submitButton.disabled = true;
        submitButton.textContent = "上传中...";
    }

    const payload = new FormData(form);
    if (qrcodePageState.courseId && !payload.get("course_id")) {
        payload.set("course_id", qrcodePageState.courseId);
    }

    const result = await qrcodeApi("/api/qrcode/uploads", {
        method: "POST",
        body: payload,
    });

    if (result.success) {
        form.reset();
        if (qrcodePageState.courseId) {
            const hiddenCourseId = form.querySelector('input[name="course_id"]');
            if (hiddenCourseId) hiddenCourseId.value = qrcodePageState.courseId;
        }
        updateContributorStats({
            email: result.stats?.email || "",
            stats: result.stats || {},
        });
        await loadQrcodeContext();
        await loadQrcodeUploads();
        setQrcodeStatus(result.message || "上传成功，二维码已加入共享列表", "success");
    } else {
        setQrcodeStatus(result.error || "上传失败，请稍后重试", "error");
    }

    if (submitButton) {
        submitButton.disabled = false;
        submitButton.textContent = "上传二维码";
    }
}

document.addEventListener("DOMContentLoaded", () => {
    qrcodePageState.courseId = document.body?.dataset?.courseId || "";

    const form = document.getElementById("qrcodeUploadForm");
    if (form) {
        form.addEventListener("submit", submitQrcodeForm);
    }

    const refreshButton = document.getElementById("qrcodeRefreshButton");
    if (refreshButton) {
        refreshButton.addEventListener("click", async () => {
            setQrcodeStatus("正在刷新二维码列表...");
            await loadQrcodeUploads();
            setQrcodeStatus("二维码列表已刷新");
        });
    }

    loadQrcodeContext();
    loadQrcodeUploads();
});

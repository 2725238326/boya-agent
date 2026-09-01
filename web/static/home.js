async function fetchJson(url) {
    const resp = await fetch(url, {
        headers: { Accept: 'application/json' },
    });

    const contentType = (resp.headers.get('content-type') || '').toLowerCase();
    if (!contentType.includes('application/json')) {
        throw new Error(`Non-JSON response: ${resp.status}`);
    }

    const data = await resp.json();
    if (!resp.ok || !data.success) {
        throw new Error(data.error || `Request failed: ${resp.status}`);
    }
    return data;
}

async function loadHomeInsights() {
    const availableEl = document.getElementById('availableCount');
    const activeEl = document.getElementById('activeCount');
    const nextNameEl = document.getElementById('nextEnrollName');
    const nextHintEl = document.getElementById('nextEnrollHint');
    const generatedAtEl = document.getElementById('homeGeneratedAt');

    try {
        const data = await fetchJson('/api/public/insights');
        const payload = data.data || {};

        availableEl.textContent = payload.available_count ?? '-';
        activeEl.textContent = payload.active_count ?? '-';
        generatedAtEl.textContent = payload.generated_at || '刚刚更新';

        if (payload.next_enroll) {
            nextNameEl.textContent = payload.next_enroll.course_name || '即将开抢';
            nextHintEl.textContent = formatCountdown(payload.next_enroll.seconds_left || 0);
        } else {
            nextNameEl.textContent = '暂无即将开抢课程';
            nextHintEl.textContent = '有新课程进入开抢窗口后，这里会自动更新。';
        }
    } catch (err) {
        generatedAtEl.textContent = '暂不可用';
        nextNameEl.textContent = '数据加载失败';
        nextHintEl.textContent = '请稍后刷新首页重试。';
    }
}

async function loadHomeSession() {
    const portalBtn = document.getElementById('portalEntryBtn');
    if (!portalBtn) return;

    try {
        const data = await fetchJson('/api/subscriber/session');
        if (!data.data || !data.data.email) return;
        portalBtn.textContent = '我的门户';
        portalBtn.href = '/portal';
    } catch (err) {
        // Logged-out users can keep the default portal entry link.
    }
}

function formatCountdown(secondsLeft) {
    const total = Math.max(0, Number(secondsLeft || 0));
    if (total < 60) return '1 分钟内开抢';
    if (total < 3600) return `${Math.floor(total / 60)} 分钟后开抢`;

    if (total < 86400) {
        const hours = Math.floor(total / 3600);
        const minutes = Math.floor((total % 3600) / 60);
        return minutes ? `${hours} 小时 ${minutes} 分后开抢` : `${hours} 小时后开抢`;
    }

    const days = Math.floor(total / 86400);
    const hours = Math.floor((total % 86400) / 3600);
    return hours ? `${days} 天 ${hours} 小时后开抢` : `${days} 天后开抢`;
}

document.addEventListener('DOMContentLoaded', () => {
    loadHomeInsights();
    loadHomeSession();
});

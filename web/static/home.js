async function loadHomeInsights() {
    const availableEl = document.getElementById('availableCount');
    const activeEl = document.getElementById('activeCount');
    const nextNameEl = document.getElementById('nextEnrollName');
    const nextHintEl = document.getElementById('nextEnrollHint');
    const generatedAtEl = document.getElementById('homeGeneratedAt');

    try {
        const resp = await fetch('/api/public/insights', {
            headers: { 'Accept': 'application/json' },
        });
        const data = await resp.json();
        if (!resp.ok || !data.success || !data.data) {
            throw new Error(data.error || 'load insights failed');
        }

        const payload = data.data;
        availableEl.textContent = payload.available_count ?? '-';
        activeEl.textContent = payload.active_count ?? '-';
        generatedAtEl.textContent = payload.generated_at || '刚刚更新';

        if (payload.next_enroll) {
            nextNameEl.textContent = payload.next_enroll.course_name || '即将开抢';
            nextHintEl.textContent = formatCountdown(payload.next_enroll.seconds_left || 0);
        } else {
            nextNameEl.textContent = '暂无即将开抢课程';
            nextHintEl.textContent = '系统会在发现近期开抢课程后更新这里';
        }
    } catch (err) {
        generatedAtEl.textContent = '暂不可用';
        nextNameEl.textContent = '数据加载失败';
        nextHintEl.textContent = '请稍后刷新首页重试';
    }
}

async function loadHomeSession() {
    const portalBtn = document.getElementById('portalEntryBtn');
    if (!portalBtn) return;

    try {
        const resp = await fetch('/api/subscriber/session', {
            headers: { 'Accept': 'application/json' },
        });
        if (!resp.ok) return;
        const data = await resp.json();
        if (!data.success || !data.data || !data.data.email) return;
        portalBtn.textContent = '继续进入我的门户';
        portalBtn.href = `/portal?email=${encodeURIComponent(data.data.email)}`;
    } catch (err) {
        // Keep the default CTA when the user is not logged in.
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

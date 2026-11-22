/* Three-minute guided demo: every step drives the real workspace handlers
   (dataset load, event click, replay transport), never a hardcoded shortcut. */

const $ = (selector) => document.querySelector(selector);

export function createDemoGuide({go, getAnalysis, openDrawer}) {
  const bar = $("#demo-guide");
  const indexEl = $("#guide-index"), titleEl = $("#guide-title"), textEl = $("#guide-text");
  const prevBtn = $("#guide-prev"), nextBtn = $("#guide-next");
  const resetBtn = $("#guide-reset"), exitBtn = $("#guide-exit");
  let active = false, index = 0, done = false, highlighted = null;

  const click = (selector) => { const node = $(selector); if (node) node.click(); return node; };
  const analysis = () => getAnalysis();

  const steps = [
    {
      title: "了解系统定位",
      text: "这是 RailVehicle-Lab 在线参赛版：在浏览器本地完成转向架三维检视与运行数据异常定位，证据可溯源到原始 CSV 行号。左侧五个入口均可直接操作，无需账号或密钥。",
      target: "#home .home-hero",
      enter: async () => { go("home"); },
    },
    {
      title: "体验示例数据",
      text: "已载入「虚构演示批次 1」。注意顶部琥珀色徽标：默认示例为虚构教学数据、非实测；若导入自己的 CSV，仅在浏览器本地解析、不向云端上传。",
      target: "#data-badge",
      enter: async () => { const a = analysis(); if (a) await a.loadDemo(0); go("analysis"); },
    },
    {
      title: "查看运行曲线",
      text: "三条通道共用时间轴：速度（蓝）、横向加速度（青）、垂向加速度（琥珀）。红点为超阈值样本，虚线为 ±阈值，色带为异常区间。点击或拖动曲线即可定位时间。",
      target: "#charts",
      enter: async () => { go("analysis"); },
    },
    {
      title: "点击一项异常事件",
      text: "已为你真实点击第一项异常事件。右侧事件列表给出持续时长、峰值与原始 CSV 行号范围；你也可以自行点击其它事件或调整阈值重新检测。",
      target: "#sec-events",
      enter: async () => { go("analysis"); click("#sec-events-body .event-item"); },
    },
    {
      title: "自动定位时间与原始样本",
      text: "时间轴与曲线游标已同步到该事件起始样本。读数区显示 UTC 时间、样本序号与「CSV 第 N 行」，与原始文件行号一一对应，可直接回查源数据。",
      target: "#timeline .readout",
      enter: async () => { go("analysis"); },
    },
    {
      title: "观察三维教学回放",
      text: "已开始回放：三维构架与轮对按事件类别做预设教学运动（类别 C），相关结构同步高亮。它是教学示意，并非由加速度积分重建的实车运动。",
      target: "#stage",
      enter: async () => {
        go("analysis");
        const play = $("#tl-play");
        if (play && play.getAttribute("aria-pressed") !== "true") play.click();
      },
    },
    {
      title: "查看证据与工程局限",
      text: "证据面板给出判定规则（|a| > 阈值，严格大于、分轴统计）、起止与峰值观测表、原始行号和相关结构示意，并明确声明：统计异常不构成故障诊断、运行安全或法规结论。",
      target: "#sec-detail",
      enter: async () => {
        go("analysis");
        const play = $("#tl-play");
        if (play && play.getAttribute("aria-pressed") === "true") play.click();
        const sec = $("#sec-detail"); if (sec) sec.open = true;
      },
    },
  ];

  function highlight(selector) {
    if (highlighted) highlighted.classList.remove("guide-target");
    highlighted = null;
    if (!selector) return;
    const node = $(selector);
    if (!node) return;
    node.classList.add("guide-target");
    highlighted = node;
    node.scrollIntoView({block: "nearest", behavior: "smooth"});
    if (node.closest("#panel") && window.innerWidth <= 720) openDrawer();
  }

  function render() {
    indexEl.textContent = done ? "完成" : `${index + 1} / ${steps.length}`;
    titleEl.textContent = done ? "演示完成" : steps[index].title;
    textEl.textContent = done
      ? "你已走完「定位 → 示例数据 → 曲线 → 异常 → 行号 → 三维回放 → 证据」的完整闭环。可重置后重新体验，或退出导览自由探索；导入自己的 CSV 也能复现同一流程。"
      : steps[index].text;
    prevBtn.disabled = done ? false : index === 0;
    nextBtn.textContent = done ? "重新体验" : index === steps.length - 1 ? "完成演示" : "下一步";
  }

  async function setStep(next) {
    index = next; done = false;
    render();
    await steps[index].enter();
    highlight(steps[index].target);
  }

  async function start() { active = true; bar.hidden = false; await setStep(0); }
  function exit() {
    active = false; bar.hidden = true;
    if (highlighted) highlighted.classList.remove("guide-target");
    highlighted = null;
  }
  async function next() {
    if (done) { await setStep(0); return; }
    if (index < steps.length - 1) await setStep(index + 1);
    else { done = true; render(); highlight(null); }
  }
  async function prev() {
    if (done) { await setStep(steps.length - 1); return; }
    if (index > 0) await setStep(index - 1);
  }
  async function reset() {
    const a = analysis(); if (a) await a.loadDemo(0);
    go("analysis");
    await setStep(2);
  }

  prevBtn.addEventListener("click", prev);
  nextBtn.addEventListener("click", next);
  resetBtn.addEventListener("click", reset);
  exitBtn.addEventListener("click", exit);

  return {
    start, exit, next, prev, reset,
    get active() { return active; },
    get step() { return index; },
    get done() { return done; },
  };
}
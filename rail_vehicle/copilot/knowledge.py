"""Local retrieval over attributed engineering notes. Retrieved text is data."""

from dataclasses import dataclass
import re

from rail_vehicle.bogie import PART_DESCRIPTIONS


@dataclass(frozen=True)
class Passage:
    id: str
    title: str
    source: str
    text: str


CATALOG = tuple(
    Passage("parts." + str(i), name, "RailVehicle-Lab · bogie.PART_DESCRIPTIONS", description)
    for i, (name, description) in enumerate(PART_DESCRIPTIONS.items(), 1)
) + (
    Passage(
        "signal.welch",
        "FFT、峭度与 Welch 功率谱密度",
        "https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.welch.html",
        "Welch 方法对分段信号的功率谱估计取平均。项目先核对时间间隔、重复时间戳、记录长度和奈奎斯特频率。谱峰描述频率成分，识别激励来源还需要转速或其他同步通道。",
    ),
    Passage(
        "signal.threshold",
        "横向与垂向振动异常回查",
        "RailVehicle-Lab · anomaly.py / event_anchor.py",
        "本项目逐轴使用加速度绝对值严格大于阈值的规则，按连续样本合并区段。先复核传感器方向、采样间隔、速度和原始波形，再比较同工况记录。单通道超阈值不足以确诊蛇行、弹簧或轴承故障。",
    ),
    Passage(
        "dynamics.support",
        "悬挂刚度、阻尼与受迫振动",
        "https://openstax.org/books/university-physics-volume-1/pages/15-6-forced-oscillations",
        "受迫响应取决于激励频率、刚度、质量与阻尼。比较刚度时应固定其他参数和支承输入，同时观察行程与加速度。",
    ),
    Passage(
        "dynamics.vehicle_vertical",
        "整车浮沉、点头与固有模态",
        "RailVehicle-Lab · docs/phase9-dynamics.md",
        "小角度下，车体前后支承垂向运动是浮沉加上纵向偏距乘点头角。固有频率由质量与刚度矩阵决定。"
        "在不同速度下通过同一空间轨面时，激励频率与轴间时延同时变化。轮轨采用静平衡附近的线性刚度。",
    ),
    Passage(
        "model.track",
        "轮轨接触与双轴点头",
        "RailVehicle-Lab · docs/project-evolution.md",
        "双轴算例按轴距与速度给定前后轮座输入的时间差，再计算构架浮沉与点头。轮轨接触力、磨耗和脱轨安全指标不在此模型的求解范围内。",
    ),
    Passage(
        "dynamics.coupled_vehicle",
        "19 自由度整车虚拟试验、侧滚与摇头",
        "RailVehicle-Lab · docs/evolution-iii-lab.md",
        "对称线性模型使用二系悬挂连接车体和前后构架，一系连接构架和两组轮对。"
        "轮对垂向响应参与求解，横向轮轨位移作为规定输入。"
        "参数扫描在有限候选网格里比较车体横向加速度 RMS，不能据此判断蛇行临界速度或实车性能。",
    ),
)


def _terms(text):
    words = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", text.lower())
    return {
        token
        for word in words
        for token in (
            [word] if word.isascii() or len(word) == 1 else [word[i : i + 2] for i in range(len(word) - 1)]
        )
    }


def search(query, extra=(), *, builtin=True, limit=3):
    terms = _terms(query)
    scored = [
        (3 * len(terms & _terms(p.title)) + len(terms & _terms(p.text)), p)
        for p in (*(CATALOG if builtin else ()), *extra)
    ]
    return [p for score, p in sorted(scored, key=lambda pair: -pair[0]) if score][:limit]


def markdown_passages(content, title):
    """Split explicitly supplied, licensed notes, without following links or embedded commands."""
    if len(content) > 128_000:
        raise ValueError("知识附件上限为 128 KB。")
    import hashlib

    try:
        body = content.decode("utf-8-sig")
    except UnicodeError:
        raise ValueError("请将 Markdown 资料保存为 UTF-8 编码。") from None
    doc_id = hashlib.sha256(content).hexdigest()[:16]
    passages = []
    for block in re.split(r"\n\s*\n", body):
        for start in range(0, len(block), 900):
            snippet = block[start : start + 900].strip()
            if snippet:
                if len(passages) >= 512:
                    raise ValueError("每份资料最多索引 512 段，请按章节拆分。")
                passages.append(
                    Passage(
                        f"local.{doc_id}.{len(passages) + 1}",
                        title[:100],
                        "用户授权的本机会话资料",
                        snippet,
                    )
                )
    return tuple(passages)


def document_passages(content, title):
    if title.lower().endswith(".md"):
        return markdown_passages(content, title)
    if not title.lower().endswith(".pdf") or not content.startswith(b"%PDF-") or len(content) > 2_000_000:
        raise ValueError("请选择 Markdown 或 2 MB 以内的文字型 PDF。")
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ValueError(
            "本机未安装 PDF 文字提取组件，可使用 Markdown 或安装项目 knowledge 可选依赖。"
        ) from None
    from io import BytesIO

    try:
        reader = PdfReader(BytesIO(content))
        if reader.is_encrypted or not 1 <= len(reader.pages) <= 40:
            raise ValueError
        total = 0
        passages = []
        for i, page in enumerate(reader.pages, 1):
            body = (page.extract_text() or "").encode("utf-8")
            total += len(body)
            if total > 128_000:
                raise ValueError
            for p in markdown_passages(body, title):
                passages.append(Passage(f"{p.id}.p{i}", p.title, f"用户授权 PDF · 第 {i} 页", p.text))
        if not 1 <= len(passages) <= 512:
            raise ValueError
        return tuple(passages)
    except Exception:
        raise ValueError("无法提取可检索文字，请使用 40 页以内的未加密文字型 PDF 或 Markdown。") from None

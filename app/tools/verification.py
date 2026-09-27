from datetime import UTC, datetime, timedelta
from math import isclose, isfinite
from statistics import median
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime

from app.contracts.common import ContractModel
from app.contracts.tool import ToolOutput
from app.tools.analytics import (
    AnomalyInput,
    AnomalyOutput,
    MeterSeries,
    PeerComparisonInput,
    PeerComparisonOutput,
    ReadingPoint,
    _percentile,
    compare_with_peers,
)

# Version للـPeer Agreement policy.
# فائدتها نعرف بأي methodology تم اتخاذ القرار لو تغيرت الطريقة مستقبلاً.
METHOD_VERSION = "peer_agreement_v1"
VERIFICATION_METHOD_VERSION = "verification_v1"

# لازم يكون عندنا على الأقل 12 historical comparisons صالحة
# قبل ما نعتبر الـhistorical peer relationship موثوقة.
# الرقم 12 مأخوذ من existing peer-selection contract وليس رقم عشوائي جديد.
MIN_HISTORICAL_SUPPORT = 12


class PeerAgreementReference(ContractModel):
    """
    يمثل الـhistorical reference للعلاقة الطبيعية
    بين target meter ومجموعة peers ثابتة قبل الحدث.
    """

    # answered = قدرنا نبني reference صالح.
    # unknown = ما عندنا evidence كافية لبناء reference.
    status: Literal["answered", "unknown"]

    # الـmeter الذي بُني هذا الـreference خصيصاً له.
    target_meter_id: str

    # الـpeers الذين استخدمناهم لبناء العلاقة التاريخية.
    # لازم يبقوا نفسهم وقت الـverification.
    peer_ids: tuple[str, ...]

    # أي historical data مستخدمة لازم تكون strictly before هذا الوقت.
    # هذا يمنع Future Leakage.
    cutoff: AwareDatetime | None = None

    # عدد historical peer comparisons الصالحة التي بُني منها الـreference.
    support_count: int

    # المجال الطبيعي للعلاقة التاريخية.
    # مثال: M1 تاريخياً يكون بين -8% و -4% مقارنة بالـpeers.
    lower: float | None = None
    upper: float | None = None
    median: float | None = None
    q1: float | None = None
    q3: float | None = None
    iqr: float | None = None
    tolerance: float | None = None

    # نسخة الـpolicy المستخدمة.
    method_version: Literal["peer_agreement_v1"] = METHOD_VERSION

    # سبب مفيد خصوصاً عندما يكون status = unknown.
    reason: str | None = None


class PeerAgreementResult(ToolOutput):
    """
    النتيجة الحالية:
    هل علاقة target meter الحالية مع peers
    ما زالت ضمن الـhistorical relationship الطبيعية؟
    """

    # pass    = العلاقة الحالية ضمن historical range.
    # fail    = عندنا evidence صالح لكنها خارج historical range.
    # unknown = evidence ناقصة أو غير متوافقة، لذلك لا نستطيع الحكم.
    status: Literal["pass", "fail", "unknown"]

    # signed deviation الحالية القادمة أصلاً من PeerComparisonOutput.
    # لا نستخدم abs() لأن العلاقة الطبيعية نفسها ممكن تكون سالبة مثل -50%.
    current_deviation_pct: float | None = None

    # الـhistorical bounds التي استخدمناها لاتخاذ القرار.
    lower: float | None = None
    upper: float | None = None
    median: float | None = None
    q1: float | None = None
    q3: float | None = None
    iqr: float | None = None
    tolerance: float | None = None

    # عدد observations التاريخية خلف الـreference.
    support_count: int = 0

    method_version: Literal["peer_agreement_v1"] = METHOD_VERSION

    # شرح واضح لماذا النتيجة Pass / Fail / Unknown.
    reason: str
class ReturnToBaselineResult(ToolOutput):
    """
    هل القراءة الحالية رجعت ضمن الـhistorical baseline الطبيعي؟
    """

    # pass    = القراءة الحالية لا يظهر عليها anomaly مع evidence صالحة.
    # fail    = القراءة الحالية ما زالت anomalous.
    # unknown = ما عندنا evidence كافية للحكم.
    status: Literal["pass", "fail", "unknown"]

    # شرح سبب القرار.
    reason: str
class ObservationEvidence(ContractModel):
    """
    Reading واحدة مع نتيجة الـreliability الخاصة فيها.

    data_reliable:
    True  = القراءة صالحة للاستخدام
    False = القراءة غير موثوقة
    None  = reliability غير معروفة
    """

    reading: ReadingPoint
    data_reliable: bool | None = None


class ObservationWindowResult(ToolOutput):
    """
    هل الـobservation window فيها evidence كافية لبدء verification؟

    ready     = كل expected slots موجودة وصالحة
    not_ready = في مشكلة معروفة مثل missing/duplicate/unreliable reading
    unknown   = window specification أو reliability غير قابلة للحسم
    """

    status: Literal["ready", "not_ready", "unknown"]
    expected_count: int = 0
    usable_count: int = 0
    reason: str
class VerificationSummaryResult(ToolOutput):
    """
    القرار النهائي للـdeterministic verification.

    verified:
        كل evidence المطلوبة موجودة،
        والعداد رجع للـbaseline،
        وعلاقته مع peers رجعت طبيعية.

    not_verified:
        evidence كاملة لكن baseline أو peer agreement فشلت.

    insufficient_evidence:
        evidence ناقصة أو إحدى النتائج Unknown.
    """

    status: Literal[
        "verified",
        "not_verified",
        "insufficient_evidence",
    ]

    # نخزن component statuses حتى يكون القرار explainable/auditable.
    window_status: Literal["ready", "not_ready", "unknown"]
    baseline_status: Literal["pass", "fail", "unknown"]
    peer_status: Literal["pass", "fail", "unknown"]

    method_version: Literal["verification_v1"] = VERIFICATION_METHOD_VERSION
    reason: str
def build_peer_agreement_reference(
    *,
    target: MeterSeries,
    peers: tuple[MeterSeries, ...],
    cutoff: datetime,
    run_id: UUID,
) -> PeerAgreementReference:
    """Build a historical signed peer-deviation envelope before cutoff."""

    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise ValueError("cutoff must be timezone-aware")

    peer_ids = tuple(peer.meter_id for peer in peers)
    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        return PeerAgreementReference(
        status="unknown",
        target_meter_id=target.meter_id,
        peer_ids=tuple(peer.meter_id for peer in peers),
        cutoff=None,
        support_count=0,
        lower=None,
        upper=None,
        reason="Cutoff timestamp must be timezone-aware.",
    )
    cutoff_utc = cutoff.astimezone(UTC)

    def unknown(reason: str, support_count: int = 0) -> PeerAgreementReference:
        return PeerAgreementReference(
            status="unknown",
            target_meter_id=target.meter_id,
            peer_ids=peer_ids,
            cutoff=cutoff_utc,
            support_count=support_count,
            reason=reason,
        )

    if not peer_ids:
        return unknown("Frozen peer set is empty.")
    if len(peer_ids) != len(set(peer_ids)):
        return unknown("Frozen peer set contains duplicate meter IDs.")
    if target.meter_id in peer_ids:
        return unknown("Target meter is included in the frozen peer set.")

    timestamps = set()
    for point in target.readings:
        if point.timestamp.tzinfo is not None and point.timestamp.utcoffset() is not None:
            timestamp_utc = point.timestamp.astimezone(UTC)
            if timestamp_utc < cutoff_utc:
                timestamps.add(timestamp_utc)

    deviations: list[float] = []
    for timestamp in sorted(timestamps):
        series = (target, *peers)
        readings_by_meter = [
            [point for point in meter.readings if point.timestamp == timestamp]
            for meter in series
        ]
        if any(len(points) != 1 for points in readings_by_meter):
            continue
        values = [points[0].kwh for points in readings_by_meter]
        if any(value is None or not isfinite(value) or value < 0 for value in values):
            continue

        comparison = compare_with_peers(
            PeerComparisonInput(
                run_id=run_id,
                target=target,
                peers=list(peers),
                event_time=timestamp,
            )
        )
        if (
            comparison.status != "answered"
            or comparison.peer_count != len(peer_ids)
            or comparison.peer_median_kwh is None
            or not isfinite(comparison.peer_median_kwh)
            or comparison.peer_median_kwh == 0
            or comparison.deviation_pct is None
            or not isfinite(comparison.deviation_pct)
        ):
            continue
        deviations.append(comparison.deviation_pct)

    if len(deviations) < MIN_HISTORICAL_SUPPORT:
        return unknown("Historical peer deviation support is insufficient.", len(deviations))

    historical_median = median(deviations)
    q1 = _percentile(deviations, 0.25)
    q3 = _percentile(deviations, 0.75)
    iqr = q3 - q1
    tolerance = max(1.5 * iqr, 0.10 * abs(historical_median), 0.01)
    lower = q1 - tolerance
    upper = q3 + tolerance

    return PeerAgreementReference(
        status="answered",
        target_meter_id=target.meter_id,
        peer_ids=peer_ids,
        cutoff=cutoff_utc,
        support_count=len(deviations),
        lower=lower,
        upper=upper,
        median=historical_median,
        q1=q1,
        q3=q3,
        iqr=iqr,
        tolerance=tolerance,
    )

def evaluate_peer_agreement(
    *,
    reference: PeerAgreementReference | None,
    current: PeerComparisonOutput,
    target_meter_id: str,
    current_peer_ids: tuple[str, ...],
    comparison_timestamp: datetime,
) -> PeerAgreementResult:
    """
    يقارن الـcurrent peer relationship مع historical reference جاهز.

    هذه الـfunction لا:
    - تختار peers.
    - تحسب peer median.
    - تحسب deviation_pct.
    - تبني historical reference.

    هي فقط تقوم بـvalidation ثم تعطي:
    pass / fail / unknown.
    """

    # 1) بدون historical reference ما بنقدر نحكم.
    if reference is None or reference.status != "answered":
        return PeerAgreementResult(
            status="unknown",
            reason="Historical peer agreement reference is unavailable.",
        )

    # 2) نتأكد أن الـreference نفسه مبني على history كافية.
    if reference.support_count < MIN_HISTORICAL_SUPPORT:
        return PeerAgreementResult(
            status="unknown",
            support_count=reference.support_count,
            reason="Historical peer agreement support is insufficient.",
        )

    # 3) نتأكد أن historical bounds موجودة ومنطقية.
    if (
        reference.lower is None
        or reference.upper is None
        or not isfinite(reference.lower)
        or not isfinite(reference.upper)
        or reference.lower > reference.upper
    ):
        return PeerAgreementResult(
            status="unknown",
            support_count=reference.support_count,
            reason="Historical peer agreement bounds are unavailable or invalid.",
        )

    # 4) الـreference لازم يحتوي peers صالحين وغير مكررين.
    if (
        not reference.peer_ids
        or len(reference.peer_ids) != len(set(reference.peer_ids))
        or reference.target_meter_id in reference.peer_ids
    ):
        return PeerAgreementResult(
            status="unknown",
            lower=reference.lower,
            upper=reference.upper,
            support_count=reference.support_count,
            reason="Historical peer membership is invalid.",
        )
    # نتأكد أن target IDs ليست empty أو whitespace فقط.
    if not reference.target_meter_id.strip() or not target_meter_id.strip():
        return PeerAgreementResult(
            status="unknown",
            lower=reference.lower,
            upper=reference.upper,
            support_count=reference.support_count,
            reason="Target meter identifier is empty or invalid.",
        )

    # نتأكد أن historical peer IDs كلها صالحة.
    if any(not peer_id.strip() for peer_id in reference.peer_ids):
        return PeerAgreementResult(
            status="unknown",
            lower=reference.lower,
            upper=reference.upper,
            support_count=reference.support_count,
            reason="Historical peer membership contains an empty identifier.",
        )
    # 5) ممنوع نستخدم reference معمول لـmeter آخر.
    if reference.target_meter_id != target_meter_id:
        return PeerAgreementResult(
            status="unknown",
            lower=reference.lower,
            upper=reference.upper,
            support_count=reference.support_count,
            reason="Target meter does not match the historical peer reference.",
        )
    # نتأكد أن current peer IDs ليست empty أو whitespace فقط.
    if any(not peer_id.strip() for peer_id in current_peer_ids):
        return PeerAgreementResult(
            status="unknown",
            lower=reference.lower,
            upper=reference.upper,
            support_count=reference.support_count,
            reason="Current peer membership contains an empty identifier.",
        )
    # 6) IDs الخاصة بالـcurrent peers لازم تكون unique.
    if len(current_peer_ids) != len(set(current_peer_ids)):
        return PeerAgreementResult(
            status="unknown",
            lower=reference.lower,
            upper=reference.upper,
            support_count=reference.support_count,
            reason="Current peer membership contains duplicate meter IDs.",
        )

    # 7) لازم نستخدم نفس frozen peers تاريخياً وحالياً.
    # الترتيب غير مهم، لذلك نستخدم set.
    if set(current_peer_ids) != set(reference.peer_ids):
        return PeerAgreementResult(
            status="unknown",
            lower=reference.lower,
            upper=reference.upper,
            support_count=reference.support_count,
            reason="Current peer membership does not match the historical reference.",
        )

    # 8) peer_count القادم من PeerComparisonOutput
    # لازم يطابق عدد الـpeer IDs التي ساهمت فعلياً بالمقارنة.
    if current.peer_count != len(current_peer_ids):
        return PeerAgreementResult(
            status="unknown",
            lower=reference.lower,
            upper=reference.upper,
            support_count=reference.support_count,
            reason="Peer count does not match the contributing peer IDs.",
        )

    # 9) لازم timestamp يكون timezone-aware.
    if (
        comparison_timestamp.tzinfo is None
        or comparison_timestamp.utcoffset() is None
    ):
        return PeerAgreementResult(
            status="unknown",
            lower=reference.lower,
            upper=reference.upper,
            support_count=reference.support_count,
            reason="Comparison timestamp must be timezone-aware.",
        )

    # 10) Current comparison لازم تكون عند أو بعد historical cutoff.
    # الـObservation Window لاحقاً هو المسؤول عن full post-action interval rules.
    if comparison_timestamp < reference.cutoff:
        return PeerAgreementResult(
            status="unknown",
            lower=reference.lower,
            upper=reference.upper,
            support_count=reference.support_count,
            reason="Comparison timestamp is earlier than the historical cutoff.",
        )

    # 11) answered هنا معناها أن Peer Comparison قدرت تنتج evidence.
    # لا تعني أن الـmeters متفقين.
    if current.status != "answered":
        return PeerAgreementResult(
            status="unknown",
            lower=reference.lower,
            upper=reference.upper,
            support_count=reference.support_count,
            reason="Current peer comparison is unavailable.",
        )

    # 12) Peer median = 0 يخلي percentage deviation غير قابلة للحساب.
    if (
        current.peer_median_kwh is None
        or not isfinite(current.peer_median_kwh)
        or current.peer_median_kwh == 0
    ):
        return PeerAgreementResult(
            status="unknown",
            lower=reference.lower,
            upper=reference.upper,
            support_count=reference.support_count,
            reason="Current peer median is unavailable or zero.",
        )

    # 13) لازم تكون signed deviation الحالية موجودة وصالحة.
    if current.deviation_pct is None or not isfinite(current.deviation_pct):
        return PeerAgreementResult(
            status="unknown",
            lower=reference.lower,
            upper=reference.upper,
            support_count=reference.support_count,
            reason="Current peer deviation is unavailable or invalid.",
        )

    # 14) هذا هو الـactual Peer Agreement decision.
    # Inclusive boundaries:
    # lower <= deviation <= upper
    #
    # ما بنستخدم abs() لأن historical relationship ممكن تكون مثلاً -50%.
    is_agreement = (
        reference.lower
        <= current.deviation_pct
        <= reference.upper
    )

    # 15) إذا evidence كلها صالحة:
    # داخل historical range  -> pass
    # خارج historical range -> fail
    return PeerAgreementResult(
        status="pass" if is_agreement else "fail",
        current_deviation_pct=current.deviation_pct,
        lower=reference.lower,
        upper=reference.upper,
        support_count=reference.support_count,
        reason=(
            "Current peer relationship is within the historical range."
            if is_agreement
            else "Current peer relationship is outside the historical range."
        ),
    )
def evaluate_return_to_baseline(
    *,
    anomaly_input: AnomalyInput | None,
    anomaly_result: AnomalyOutput | None,
    comparison_timestamp: datetime,
    data_reliable: bool | None,
) -> ReturnToBaselineResult:
    """
    يحدد هل current observation رجعت للـhistorical baseline.

    لا يعيد حساب baseline أو anomaly.
    يستخدم فقط existing Day 1–5 evidence.
    """

    # 1) الـquality gate أولاً.
    # Missing/unreliable data لا يمكن اعتبارها recovery.
    if data_reliable is not True:
        return ReturnToBaselineResult(
            status="unknown",
            reason="Current reading quality is unavailable or unreliable.",
        )

    # 2) لازم يكون عندنا نفس الـinput والـresult المستخدمين بالتحليل.
    if anomaly_input is None or anomaly_result is None:
        return ReturnToBaselineResult(
            status="unknown",
            reason="Anomaly evidence is unavailable.",
        )

    # 3) لازم comparison timestamp يكون timezone-aware.
    if (
        comparison_timestamp.tzinfo is None
        or comparison_timestamp.utcoffset() is None
    ):
        return ReturnToBaselineResult(
            status="unknown",
            reason="Comparison timestamp must be timezone-aware.",
        )

    # 4) لازم تكون القراءة المطلوبة موجودة exactly once.
    matching_readings = [
        reading
        for reading in anomaly_input.readings
        if reading.timestamp == comparison_timestamp
    ]

    if len(matching_readings) != 1:
        return ReturnToBaselineResult(
            status="unknown",
            reason="Current reading is missing or duplicated.",
        )

    current_reading = matching_readings[0]

    # 5) لازم current kWh تكون موجودة ورقم finite.
    if current_reading.kwh is None or not isfinite(current_reading.kwh):
        return ReturnToBaselineResult(
            status="unknown",
            reason="Current reading value is unavailable or invalid.",
        )

    # 6) نبحث عن historical baseline profile لنفس weekday/time slot.
        # نستخدم timestamp الخاصة بالـmatched reading نفسها،
    # وليس wall-clock fields القادمة من comparison_timestamp.
    # وبنفس Day 3 half-hour slot convention:
    # 12:05 -> 12:00
    # 12:35 -> 12:30
    slot_timestamp = current_reading.timestamp
    slot_minute = 0 if slot_timestamp.minute < 30 else 30

    matching_profiles = [
        profile
        for profile in anomaly_input.baseline_profiles
        if profile.weekday == slot_timestamp.weekday()
        and profile.hour == slot_timestamp.hour
        and profile.minute == slot_minute
    ]


    if len(matching_profiles) != 1:
        return ReturnToBaselineResult(
            status="unknown",
            reason="Matching historical baseline profile is unavailable.",
        )

    profile = matching_profiles[0]

    # 7) نحمي من baseline statistics غير صالحة.
    baseline_values = (
        profile.median_kwh,
        profile.q1_kwh,
        profile.q3_kwh,
        profile.iqr_kwh,
    )
    if (
        profile.sample_count <= 0
        or any(not isfinite(value) for value in baseline_values)
        or profile.q1_kwh > profile.q3_kwh
        or not (
            profile.q1_kwh
            <= profile.median_kwh
            <= profile.q3_kwh
        )
        or profile.iqr_kwh < 0
        or not isclose(
            profile.iqr_kwh,
            profile.q3_kwh - profile.q1_kwh,
            rel_tol=0.0,
            abs_tol=2e-6,
        )
    ):
         return ReturnToBaselineResult(
            status="unknown",
            reason="Historical baseline profile is invalid.",
        )

    # 8) insufficient baseline أبداً لا تعني normal.
    if anomaly_result.status == "insufficient_baseline":
        return ReturnToBaselineResult(
            status="unknown",
            reason="Anomaly analysis has insufficient baseline evidence.",
        )
        # الـflatline event قد يتم تسجيلها عند اللحظة التي وصلت فيها
    # الـrun للحد المطلوب، وليس عند كل reading لاحقة.
    # لذلك نتحقق هل flatline سابقة ما زالت مستمرة حتى current reading.
    expected_step = timedelta(
        minutes=anomaly_input.expected_interval_minutes
    )

    ordered_readings = sorted(
        [
            reading
            for reading in anomaly_input.readings
            if reading.timestamp <= comparison_timestamp
        ],
        key=lambda reading: reading.timestamp,
    )

    for event in anomaly_result.events:
        if (
            event.anomaly_type != "flatline"
            or event.timestamp > comparison_timestamp
        ):
            continue

        flatline_tail = [
            reading
            for reading in ordered_readings
            if event.timestamp
            <= reading.timestamp
            <= comparison_timestamp
        ]

        if (
            not flatline_tail
            or flatline_tail[-1].timestamp != comparison_timestamp
        ):
            continue

        # أي missing/nonfinite value تكسر دليل استمرار الـflatline.
        if any(
            reading.kwh is None or not isfinite(reading.kwh)
            for reading in flatline_tail
        ):
            continue

        same_value = all(
            reading.kwh == flatline_tail[0].kwh
            for reading in flatline_tail
        )

        contiguous = all(
            later.timestamp - earlier.timestamp == expected_step
            for earlier, later in zip(
                flatline_tail,
                flatline_tail[1:],
                strict=False,)

        )

        if same_value and contiguous:
            return ReturnToBaselineResult(
                status="fail",
                reason=(
                    "Current reading remains part of an ongoing flatline."
                ),
            )

    # 9) نهتم فقط بالأحداث المتعلقة بالـcurrent timestamp.
    current_events = [
        event
        for event in anomaly_result.events
        if event.timestamp == comparison_timestamp
    ]

    # Gap لا يثبت recovery أو failure للقراءة نفسها.
    if any(event.anomaly_type == "gap" for event in current_events):
        return ReturnToBaselineResult(
            status="unknown",
            reason="Current observation has incomplete gap evidence.",
        )

    # 10) أي anomaly فعلية على current timestamp تعني أن القراءة لم ترجع بعد.
    if current_events:
        return ReturnToBaselineResult(
            status="fail",
            reason="Current reading remains anomalous relative to its historical baseline.",
        )

    # 11) وصلنا لهون:
    # quality صالحة + reading موجودة + profile موجود +
    # anomaly evidence صالحة + لا يوجد anomaly على current timestamp.
    return ReturnToBaselineResult(
        status="pass",
        reason="Current reading is within its historical baseline behavior.",
    )
def check_observation_window(
    *,
    expected_timestamps: tuple[datetime, ...],
    observations: tuple[ObservationEvidence, ...],
    reference_timestamp: datetime,
    as_of: datetime,
) -> ObservationWindowResult:
    """
    يتحقق أن كل expected observation slots موجودة وقابلة للاستخدام.

    هذه الـfunction لا تقرر recovery.
    هي فقط تقرر هل evidence window جاهزة للـverification.
    """

    # 1) reference_timestamp و as_of لازم يكونوا timezone-aware.
    if (
        reference_timestamp.tzinfo is None
        or reference_timestamp.utcoffset() is None
        or as_of.tzinfo is None
        or as_of.utcoffset() is None
    ):
        return ObservationWindowResult(
            status="unknown",
            expected_count=len(expected_timestamps),
            reason="Reference timestamp and as-of timestamp must be timezone-aware.",
        )

    reference_utc = reference_timestamp.astimezone(UTC)
    as_of_utc = as_of.astimezone(UTC)

    # 2) ما بصير as_of يكون قبل بداية الـwindow.
    if as_of_utc < reference_utc:
        return ObservationWindowResult(
            status="unknown",
            expected_count=len(expected_timestamps),
            reason="As-of timestamp is earlier than the reference timestamp.",
        )

    # 3) لازم يكون في expected schedule أصلاً.
    if not expected_timestamps:
        return ObservationWindowResult(
            status="unknown",
            reason="Expected observation schedule is empty.",
        )

    # 4) كل expected timestamp لازم يكون timezone-aware.
    if any(
        timestamp.tzinfo is None or timestamp.utcoffset() is None
        for timestamp in expected_timestamps
    ):
        return ObservationWindowResult(
            status="unknown",
            expected_count=len(expected_timestamps),
            reason="Expected observation timestamps must be timezone-aware.",
        )

    expected_utc = tuple(
        timestamp.astimezone(UTC)
        for timestamp in expected_timestamps
    )

    # 5) ما بصير يكون عندنا duplicate expected slots.
    # تحويلهم لـUTC يخلي نفس اللحظة بتوقيتين مختلفين تعتبر نفس slot.
    if len(expected_utc) != len(set(expected_utc)):
        return ObservationWindowResult(
            status="unknown",
            expected_count=len(expected_timestamps),
            reason="Expected observation schedule contains duplicate slots.",
        )

    # 6) كل expected slot لازم تكون strictly بعد الـreference.
    if any(timestamp <= reference_utc for timestamp in expected_utc):
        return ObservationWindowResult(
            status="unknown",
            expected_count=len(expected_timestamps),
            reason="Expected observation slots must be after the reference timestamp.",
        )

    # 7) حتى الـunexpected observations لازم timestamps تبعتها تكون واضحة.
    if any(
        observation.reading.timestamp.tzinfo is None
        or observation.reading.timestamp.utcoffset() is None
        for observation in observations
    ):
        return ObservationWindowResult(
            status="unknown",
            expected_count=len(expected_timestamps),
            reason="Observation timestamps must be timezone-aware.",
        )

    usable_count = 0
    definite_problem = False
    reliability_unknown = False

    for expected_timestamp in expected_utc:
        # 8) إذا وقت الـslot لسه ما وصل، الـwindow مش جاهزة.
        if expected_timestamp > as_of_utc:
            definite_problem = True
            continue

        matches = [
            observation
            for observation in observations
            if observation.reading.timestamp.astimezone(UTC)
            == expected_timestamp
        ]

        # 9) Missing أو duplicate reading = window غير جاهزة.
        if len(matches) != 1:
            definite_problem = True
            continue

        observation = matches[0]

        # 10) إذا reliability غير معروفة، ما بنقدر نحسم.
        kwh = observation.reading.kwh

        # Missing / nonfinite / negative kWh هي مشكلة مؤكدة بالـevidence.
        # لذلك نفحصها قبل unresolved reliability.
        # Zero مسموحة لأنها ليست completeness problem.
        if kwh is None or not isfinite(kwh) or kwh < 0:
            definite_problem = True
            continue

        # إذا القراءة نفسها numerically usable لكن reliability غير معروفة،
        # ما بنقدر نحسم صلاحيتها.
        if observation.data_reliable is None:
            reliability_unknown = True
            continue

        # Reliability معروفة بأنها False => window غير جاهزة.
        if observation.data_reliable is False:
            definite_problem = True
            continue

        usable_count += 1


    # 13) Known completeness problem لها أولوية على reliability المجهولة.
    if definite_problem:
        return ObservationWindowResult(
            status="not_ready",
            expected_count=len(expected_timestamps),
            usable_count=usable_count,
            reason="Observation window is incomplete or contains unusable evidence.",
        )

    # 14) إذا ما في known blocker لكن reliability لسه مجهولة.
    if reliability_unknown:
        return ObservationWindowResult(
            status="unknown",
            expected_count=len(expected_timestamps),
            usable_count=usable_count,
            reason="Observation reliability is unavailable for one or more expected slots.",
        )

    # 15) كل expected slots موجودة وusable.
    return ObservationWindowResult(
        status="ready",
        expected_count=len(expected_timestamps),
        usable_count=usable_count,
        reason="All expected observation slots contain usable evidence.",
    )
def summarize_verification(
    *,
    window: ObservationWindowResult,
    baseline: ReturnToBaselineResult,
    peer: PeerAgreementResult,
) -> VerificationSummaryResult:
    """
    يجمع نتائج Day 6 verification بدون أي analytics جديدة.

    لا يحسب baseline.
    لا يحسب peers.
    لا يفحص readings.
    فقط يجمع النتائج الحتمية السابقة.
    """

    # 1) إذا الـobservation window نفسها غير جاهزة،
    # ما عندنا evidence كاملة لاتخاذ قرار verification.
    if window.status != "ready":
        return VerificationSummaryResult(
            status="insufficient_evidence",
            window_status=window.status,
            baseline_status=baseline.status,
            peer_status=peer.status,
            reason="Observation window does not contain sufficient usable evidence.",
        )

    # 2) حتى لو الـwindow جاهزة، أي component Unknown
    # يعني ما زلنا لا نملك evidence كافية للحكم النهائي.
    if baseline.status == "unknown" or peer.status == "unknown":
        return VerificationSummaryResult(
            status="insufficient_evidence",
            window_status=window.status,
            baseline_status=baseline.status,
            peer_status=peer.status,
            reason="Baseline or peer-agreement evidence is unavailable.",
        )

    # 3) Verification تنجح فقط إذا الاثنين Pass.
    if baseline.status == "pass" and peer.status == "pass":
        return VerificationSummaryResult(
            status="verified",
            window_status=window.status,
            baseline_status=baseline.status,
            peer_status=peer.status,
            reason=(
                "Usable observation evidence shows return to baseline "
                "and agreement with the historical peer relationship."
            ),
        )

    # 4) وصلنا لهون:
    # window جاهزة، evidence ليست Unknown،
    # لكن baseline أو peer agreement فيها Fail.
    return VerificationSummaryResult(
        status="not_verified",
        window_status=window.status,
        baseline_status=baseline.status,
        peer_status=peer.status,
        reason=(
            "Usable evidence does not satisfy all deterministic "
            "verification conditions."
        ),
    )
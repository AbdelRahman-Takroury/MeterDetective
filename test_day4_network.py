import pytest
import pandas as pd
import numpy as np
import networkx as nx
from network import build_topology_graph, get_connected_assets, detect_shared_incident


# ============================================================
# Mock Data (أسماء الأعمدة مطابقة لـ topology.csv الحقيقي من Day 2)
# ============================================================

@pytest.fixture
def mock_topology():
    return pd.DataFrame({
        'Meter_ID': ['M1', 'M2', 'M3', 'M4'],
        'Transformer_ID': ['T1', 'T1', 'T1', 'T2'],
        'Feeder_ID': ['F1', 'F1', 'F1', 'F1'],
        'Substation_ID': ['S1', 'S1', 'S1', 'S1']
    })


@pytest.fixture
def mock_readings_shared():
    """
    بنبني داتا تحاكي Shared Incident:
    M2 و M3 عندهم baseline ~2.0، لكن في اللحظة المستهدفة انخفضوا لـ 0.5 (انخفاض ~75%)
    يعني 2 من أصل 2 جيران متأثرين -> Shared
    """
    base_times = pd.date_range(
        start='2012-06-04 10:00:00',
        periods=6,
        freq='30min'
    )
    target_time = pd.Timestamp('2012-06-05 10:00:00')

    rows = []

    # قراءات M2 التاريخية (baseline ~2.0) ثم انخفاض شديد
    for t in base_times:
        rows.append({'Meter_ID': 'M2', 'DateTime': t, 'KWH/hh (per half hour)': 2.0})
    rows.append({'Meter_ID': 'M2', 'DateTime': target_time, 'KWH/hh (per half hour)': 0.5})

    # قراءات M3 التاريخية (baseline ~2.0) ثم انخفاض شديد
    for t in base_times:
        rows.append({'Meter_ID': 'M3', 'DateTime': t, 'KWH/hh (per half hour)': 1.8})
    rows.append({'Meter_ID': 'M3', 'DateTime': target_time, 'KWH/hh (per half hour)': 0.6})

    return pd.DataFrame(rows)


@pytest.fixture
def mock_readings_local():
    """
    بنبني داتا تحاكي Local Incident:
    M2 قراءته طبيعية، M3 انخفض فقط هو.
    يعني 1 من أصل 2 جيران (50%) متأثر -> Local (تحت الـ shared_threshold)
    """
    base_times = pd.date_range(
        start='2012-06-04 10:00:00',
        periods=6,
        freq='30min'
    )
    target_time = pd.Timestamp('2012-06-05 10:00:00')

    rows = []

    # M2 طبيعي تماماً في اللحظة المستهدفة
    for t in base_times:
        rows.append({'Meter_ID': 'M2', 'DateTime': t, 'KWH/hh (per half hour)': 2.0})
    rows.append({'Meter_ID': 'M2', 'DateTime': target_time, 'KWH/hh (per half hour)': 1.9})

    # M3 منخفض بشكل كبير
    for t in base_times:
        rows.append({'Meter_ID': 'M3', 'DateTime': t, 'KWH/hh (per half hour)': 1.8})
    rows.append({'Meter_ID': 'M3', 'DateTime': target_time, 'KWH/hh (per half hour)': 0.6})

    return pd.DataFrame(rows)


# ============================================================
# Test 1: بناء الـ Graph
# بنتأكد إن الـ Nodes والـ Edges اتبنوا صح
# ============================================================

def test_build_topology_graph(mock_topology):
    G = build_topology_graph(mock_topology)

    assert isinstance(G, nx.Graph)
    # 4 meters + 2 transformers (T1 و T2) = 6 nodes
    assert len(G.nodes) == 6
    assert 'M1' in G.nodes
    assert 'T1' in G.nodes
    assert G.nodes['M1']['asset_type'] == 'meter'
    assert G.nodes['T1']['asset_type'] == 'transformer'


# ============================================================
# Test 2: سحب الجيران
# بنتأكد إن get_connected_assets بترجع المحول الصح والعدادات الصح
# ============================================================

def test_get_connected_assets(mock_topology):
    G = build_topology_graph(mock_topology)

    assets = get_connected_assets(G, 'M1')

    assert assets['status'] == 'success'
    assert assets['transformer'] == 'T1'
    # M2 و M3 على T1 معنا، أما M4 فهو على T2
    assert 'M2' in assets['connected_meters']
    assert 'M3' in assets['connected_meters']
    assert 'M4' not in assets['connected_meters']


# ============================================================
# Test 3: اكتشاف Shared Incident
# بنتأكد إنه لما 2/2 جيران متأثرين (100%) يحكم بـ Shared
# ============================================================

def test_detect_shared_incident_shared(mock_readings_shared):
    result = detect_shared_incident(
        connected_meters=['M2', 'M3'],
        readings_df=mock_readings_shared,
        target_timestamp='2012-06-05 10:00:00',
        drop_threshold=0.40,
        minimum_baseline_readings=3,
        shared_threshold=0.50
    )

    assert result['status'] == 'answered'
    assert result['incident_type'] == 'Shared'
    assert result['affected_count'] == 2
    assert result['total_answered_meters'] == 2


# ============================================================
# Test 4: اكتشاف Local Incident
# بنتأكد إنه لما 1/2 جيران متأثر فقط (50%)، لكن كلهم 50% عشان نتأكد من الـ threshold
# بنعدل الـ shared_threshold لـ 0.60 عشان تكون النتيجة Local
# ============================================================

def test_detect_shared_incident_local(mock_readings_local):
    result = detect_shared_incident(
        connected_meters=['M2', 'M3'],
        readings_df=mock_readings_local,
        target_timestamp='2012-06-05 10:00:00',
        drop_threshold=0.40,
        minimum_baseline_readings=3,
        shared_threshold=0.60   # بنرفع الـ threshold عشان 50% لا تكفي للـ Shared
    )

    assert result['status'] == 'answered'
    assert result['incident_type'] == 'Local'

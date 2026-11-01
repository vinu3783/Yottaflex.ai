from plantsim.dashboard import build_dashboard


def test_dashboard_is_standalone_and_shows_dataset_metrics(starter_data, tmp_path):
    output = build_dashboard(starter_data, tmp_path / "nested" / "ops.html")
    page = output.read_text("utf-8")

    assert "<title>Plant Ops Center" in page
    assert "10,000" in page
    assert "Fault recall" in page
    assert "Detection response" in page
    assert "Alarm register" in page
    assert 'id="state-filter"' in page
    assert "dataset.state" in page
    assert "https://" not in page
    assert "src=" not in page

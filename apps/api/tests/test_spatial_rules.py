from fastapi.testclient import TestClient

from low_altitude_poc_api.app import create_app

TEST_PASSWORD = "local-test-password"


def login(client: TestClient, username: str) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": TEST_PASSWORD},
    )
    assert response.status_code == 200


def rule_draft(name: str = "观山湖核心禁飞区") -> dict:
    return {
        "rule_id": "GSH-NFZ-001",
        "name": name,
        "rule_type": "no_fly_zone",
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [106.61, 26.63],
                    [106.64, 26.63],
                    [106.64, 26.66],
                    [106.61, 26.66],
                    [106.61, 26.63],
                ]
            ],
        },
        "min_altitude_m": 60,
        "max_altitude_m": 180,
        "valid_from": "2026-09-03T00:00:00Z",
        "valid_to": "2026-09-30T00:00:00Z",
        "source": "观山湖低空运行 POC 配置",
    }


def test_only_spatial_admin_publishes_immutable_rule_versions(tmp_path):
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'spatial-rules.db'}",
        demo_password=TEST_PASSWORD,
    )

    with TestClient(app) as client:
        login(client, "situation-viewer")
        denied = client.post("/api/spatial-rules/drafts", json=rule_draft())
        assert denied.status_code == 403

        client.post("/api/auth/logout")
        login(client, "spatial-admin")
        created = client.post("/api/spatial-rules/drafts", json=rule_draft())
        first_publish = client.post("/api/spatial-rules/drafts/GSH-NFZ-001/publish")
        updated_draft = client.put(
            "/api/spatial-rules/drafts/GSH-NFZ-001",
            json=rule_draft("观山湖核心禁飞区（调整）"),
        )
        second_publish = client.post("/api/spatial-rules/drafts/GSH-NFZ-001/publish")
        mismatched_update = client.put(
            "/api/spatial-rules/drafts/GSH-NFZ-001",
            json={**rule_draft(), "rule_id": "OTHER-RULE"},
        )
        active = client.get("/api/spatial-rules/active")
        history = client.get("/api/spatial-rules/GSH-NFZ-001/versions")
        audit = client.get("/api/audit/events").json()["events"]

    assert created.status_code == 201
    assert first_publish.status_code == 201
    assert first_publish.json()["version"] == 1
    assert first_publish.json()["actor"] == "spatial-admin"
    assert updated_draft.status_code == 200
    assert second_publish.status_code == 201
    assert mismatched_update.status_code == 409
    assert second_publish.json()["version"] == 2
    assert [rule["version"] for rule in history.json()["versions"]] == [1, 2]
    assert history.json()["versions"][0]["name"] == "观山湖核心禁飞区"
    assert active.json()["rules"][0]["name"] == "观山湖核心禁飞区（调整）"
    assert active.json()["rules"][0]["coordinate_reference"] == "WGS84"
    published_audit = [
        event for event in audit if event["action"] == "spatial_rule_published"
    ]
    assert [event["subject"] for event in published_audit] == [
        "spatial_rule:GSH-NFZ-001:v1",
        "spatial_rule:GSH-NFZ-001:v2",
    ]
    draft_audit = [
        event
        for event in audit
        if event["action"]
        in {"spatial_rule_draft_saved", "spatial_rule_draft_save_failed"}
    ]
    assert [(event["action"], event["subject"]) for event in draft_audit] == [
        ("spatial_rule_draft_saved", "spatial_rule:GSH-NFZ-001"),
        ("spatial_rule_draft_saved", "spatial_rule:GSH-NFZ-001"),
        ("spatial_rule_draft_save_failed", "spatial_rule:GSH-NFZ-001"),
    ]


def test_invalid_geometry_altitude_and_time_ranges_cannot_be_published(tmp_path):
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'invalid-rules.db'}",
        demo_password=TEST_PASSWORD,
    )
    invalid_drafts = []

    invalid_geometry = rule_draft("自相交几何")
    invalid_geometry["rule_id"] = "INVALID-GEOMETRY"
    invalid_geometry["geometry"] = {
        "type": "Polygon",
        "coordinates": [
            [
                [106.61, 26.63],
                [106.64, 26.66],
                [106.64, 26.63],
                [106.61, 26.66],
                [106.61, 26.63],
            ]
        ],
    }
    invalid_drafts.append(invalid_geometry)

    reverse_altitude = rule_draft("反向高度")
    reverse_altitude.update(
        rule_id="INVALID-ALTITUDE", min_altitude_m=180, max_altitude_m=60
    )
    invalid_drafts.append(reverse_altitude)

    reverse_time = rule_draft("反向有效期")
    reverse_time.update(
        rule_id="INVALID-TIME",
        valid_from="2026-09-30T00:00:00Z",
        valid_to="2026-09-03T00:00:00Z",
    )
    invalid_drafts.append(reverse_time)

    with TestClient(app) as client:
        login(client, "spatial-admin")
        for draft in invalid_drafts:
            assert (
                client.post("/api/spatial-rules/drafts", json=draft).status_code == 201
            )
            response = client.post(
                f"/api/spatial-rules/drafts/{draft['rule_id']}/publish"
            )
            assert response.status_code == 422
        audit = client.get("/api/audit/events").json()["events"]

    assert [event["action"] for event in audit].count(
        "spatial_rule_publish_failed"
    ) == 3
    assert {
        event["subject"]
        for event in audit
        if event["action"] == "spatial_rule_publish_failed"
    } == {
        "spatial_rule:INVALID-GEOMETRY",
        "spatial_rule:INVALID-ALTITUDE",
        "spatial_rule:INVALID-TIME",
    }

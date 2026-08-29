"""Focused Sponsor/CRO detail, site-import, and schedule-version contracts."""
import asyncio
import sys
import uuid
from pathlib import Path

import httpx

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import server  # noqa: E402


LOOP = asyncio.new_event_loop()
RUN_ID = uuid.uuid4().hex[:8]
IDS = {
    "users": [], "trials": [], "patients": [], "visits": [],
    "instances": [], "sites": [], "files": [],
}


def run(coro):
    return LOOP.run_until_complete(coro)


def client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app),
        base_url="http://testserver",
    )


def headers(user):
    token = server.make_token(user["id"], user["role"])
    return {"Authorization": f"Bearer {token}"}


async def build_world():
    org_a = f"BACKEND-CONTRACT-{RUN_ID}-A"
    org_b = f"BACKEND-CONTRACT-{RUN_ID}-B"
    sponsor_a = {
        "id": str(uuid.uuid4()), "email": f"sponsor-a-{RUN_ID}@example.com",
        "full_name": "Sponsor A", "role": "sponsor", "organization": org_a,
        "org_admin": True, "status": "Active",
    }
    sponsor_b = {
        "id": str(uuid.uuid4()), "email": f"sponsor-b-{RUN_ID}@example.com",
        "full_name": "Sponsor B", "role": "sponsor", "organization": org_b,
        "org_admin": True, "status": "Active",
    }
    pi = {
        "id": str(uuid.uuid4()), "email": f"pi-{RUN_ID}@example.com",
        "full_name": "Dr Contract PI", "role": "pi",
        "organization": f"Contract Site {RUN_ID}", "status": "Active",
    }
    await server.db.users.insert_many([sponsor_a, sponsor_b, pi])
    IDS["users"].extend(user["id"] for user in (sponsor_a, sponsor_b, pi))
    organizations = [
        {"id": str(uuid.uuid4()), "name": org_a, "type": "sponsor"},
        {"id": str(uuid.uuid4()), "name": org_b, "type": "sponsor"},
    ]
    await server.db.organizations.insert_many(organizations)

    trial_a = {
        "id": str(uuid.uuid4()), "title": "Scoped Contract Trial",
        "protocol_id": f"CONTRACT-{RUN_ID}", "phase": "Phase III",
        "condition": "Testing", "sponsor_name": org_a,
        "created_by": sponsor_a["id"], "created_at": server.now(),
        "status": "active", "target_enrollment": 20,
    }
    trial_b = {
        "id": str(uuid.uuid4()), "title": "Foreign Contract Trial",
        "protocol_id": f"FOREIGN-{RUN_ID}", "phase": "Phase II",
        "condition": "Testing", "sponsor_name": org_b,
        "created_by": sponsor_b["id"], "created_at": server.now(),
        "status": "active",
    }
    await server.db.trials.insert_many([trial_a, trial_b])
    IDS["trials"].extend([trial_a["id"], trial_b["id"]])

    visits = [
        {
            "id": str(uuid.uuid4()), "trial_id": trial_a["id"],
            "visit_number": 1, "name": "Screening", "day_offset": -7,
            "window_days": 2, "activities": ["Consent"],
        },
        {
            "id": str(uuid.uuid4()), "trial_id": trial_a["id"],
            "visit_number": 2, "name": "Baseline", "day_offset": 0,
            "window_days": 3, "activities": ["Randomization"],
        },
    ]
    await server.db.visits.insert_many(visits)
    IDS["visits"].extend(row["id"] for row in visits)
    patient = {
        "id": str(uuid.uuid4()), "trial_id": trial_a["id"],
        "subject_id": f"SUBJ-{RUN_ID}", "full_name": "Must Stay Private",
        "email": f"private-{RUN_ID}@example.com", "phone": "+91 9999999999",
        "pi_id": pi["id"], "created_by": pi["id"], "status": "randomized",
        "created_at": server.now(),
    }
    await server.db.patients.insert_one(patient)
    IDS["patients"].append(patient["id"])
    instance = {
        "id": str(uuid.uuid4()), "patient_id": patient["id"],
        "trial_id": trial_a["id"], "visit_id": visits[0]["id"],
        "visit_number": 1, "name": "Screening", "status": "completed",
        "scheduled_date": server.now(), "completed_at": server.now(),
    }
    await server.db.visit_instances.insert_one(instance)
    IDS["instances"].append(instance["id"])
    document = {
        "id": str(uuid.uuid4()), "key": str(uuid.uuid4()),
        "owner_id": sponsor_a["id"],
        "scope": {"type": "trial", "id": trial_a["id"]},
        "name": "Schedule source.pdf", "content_type": "application/pdf",
        "size": 321, "created_at": server.now(),
    }
    await server.db.files.insert_one(document)
    IDS["files"].append(document["id"])
    return sponsor_a, sponsor_b, pi, trial_a, trial_b, visits, patient, document


def test_scoped_trial_detail_contracts_and_site_import():
    async def flow():
        sponsor_a, sponsor_b, pi, trial, foreign_trial, _, patient, _ = (
            await build_world())
        single_site = {
            "name": pi["organization"],
            "address": "10 Research Road",
            "city": "Mumbai",
            "state": "Maharashtra",
            "hospital_type": "Private",
            "department": "Oncology",
            "pi_name": pi["full_name"],
            "pi_email": pi["email"],
            "pi_phone": "+91 9000000000",
            "target_enrollment": 12,
            "access_type": "restricted",
        }
        async with client() as cli:
            added = await cli.post(
                f"/api/sponsor/trials/{trial['id']}/sites",
                headers=headers(sponsor_a), json=single_site)
            assert added.status_code == 200, added.text
            IDS["sites"].append(added.json()["site"]["id"])
            assert added.json()["site"]["access_type"] == "restricted"

            paths = [
                "recruitment", "subjects", "team", "documents", "versions",
            ]
            for suffix in paths:
                own = await cli.get(
                    f"/api/trials/{trial['id']}/{suffix}",
                    headers=headers(sponsor_a))
                denied = await cli.get(
                    f"/api/trials/{foreign_trial['id']}/{suffix}",
                    headers=headers(sponsor_a))
                assert own.status_code == 200, (suffix, own.text)
                assert denied.status_code == 403, (suffix, denied.text)

            journey = await cli.get(
                f"/api/trials/{trial['id']}/subjects/"
                f"{patient['subject_id']}/visits",
                headers=headers(sponsor_a))
            assert journey.status_code == 200, journey.text
            assert journey.json()[0]["status"] == "completed"
            assert "full_name" not in journey.text
            foreign_journey = await cli.get(
                f"/api/trials/{foreign_trial['id']}/subjects/"
                f"{patient['subject_id']}/visits",
                headers=headers(sponsor_a))
            assert foreign_journey.status_code == 403

            recruitment = await cli.get(
                f"/api/trials/{trial['id']}/recruitment",
                headers=headers(sponsor_a))
            site = next(row for row in recruitment.json()["sites"]
                        if row["name"] == pi["organization"])
            assert site["target_enrollment"] == 12
            assert site["recruitment"]["randomized"] == 1

            roster = (
                "name,address,city,state,hospital_type,department,pi_name,"
                "pi_email,pi_phone,target_enrollment,access_type\n"
                f"Imported Site {RUN_ID},Road 1,Pune,Maharashtra,Government,"
                f"Cardiology,Dr Imported,imported-{RUN_ID}@example.com,"
                "+91 9111111111,8,view_only\n"
                f"Broken Site {RUN_ID},Road 2,Pune,Maharashtra,Private,"
                "Cardiology,Dr Broken,not-an-email,+91 9222222222,8,full\n"
            )
            imported = await cli.post(
                f"/api/sponsor/trials/{trial['id']}/sites/import",
                headers=headers(sponsor_a),
                files={"file": ("sites.csv", roster.encode(), "text/csv")},
            )
            assert imported.status_code == 200, imported.text
            assert imported.json()["total"] == 2
            assert imported.json()["imported"] == 1
            assert imported.json()["failed"] == 1
            successful = next(row for row in imported.json()["results"]
                              if row["status"] == "imported")
            IDS["sites"].append(successful["site_id"])
            stored = await server.db.org_sites.find_one(
                {"id": successful["site_id"]}, {"_id": 0})
            assert stored["access_type"] == "view_only"
            assert trial["id"] in stored["trial_ids"]

            denied_import = await cli.post(
                f"/api/sponsor/trials/{trial['id']}/sites/import",
                headers=headers(sponsor_b),
                files={"file": ("sites.csv", roster.encode(), "text/csv")},
            )
            assert denied_import.status_code == 403
    try:
        run(flow())
    finally:
        run(cleanup())


def test_schedule_shares_pin_versions_snapshots_documents_and_real_diffs():
    async def flow():
        sponsor_a, sponsor_b, pi, trial, _, visits, _, document = (
            await build_world())
        share_body = {
            "trial_id": trial["id"], "via": "link",
            "recipients": [pi["email"]],
            "sites": [{
                "id": f"pi-{pi['id']}", "name": pi["organization"],
                "reviewer_id": pi["id"],
            }],
            "document_id": document["id"],
            "version_note": "Initial schedule",
        }
        async with client() as cli:
            first = await cli.post(
                "/api/shares", headers=headers(sponsor_a), json=share_body)
            assert first.status_code == 200, first.text
            assert first.json()["schedule_version"] == 1
            assert len(first.json()["changed_visits"]) == 2

            await server.db.visits.update_one(
                {"id": visits[1]["id"]}, {"$set": {"window_days": 7}})
            second = await cli.post(
                "/api/shares", headers=headers(sponsor_a),
                json={**share_body, "version_note": "Baseline window changed"})
            assert second.status_code == 200, second.text
            assert second.json()["schedule_version"] == 2
            assert len(second.json()["changed_visits"]) == 1
            diff = second.json()["changed_visits"][0]
            assert diff["id"] == visits[1]["id"]
            assert diff["change_type"] == "modified"
            assert diff["changed_fields"] == ["window_days"]
            assert diff["before"]["window_days"] == 3
            assert diff["after"]["window_days"] == 7

            inbox = await cli.get(
                "/api/schedule-reviews", headers=headers(pi))
            assert inbox.status_code == 200, inbox.text
            reviews = {row["schedule_version"]: row for row in inbox.json()}
            assert reviews[1]["visits"][1]["window_days"] == 3
            assert reviews[2]["visits"][1]["window_days"] == 7
            assert reviews[2]["changed_visits"][0]["changed_fields"] == [
                "window_days"]
            assert reviews[2]["document"] == {
                "id": document["id"],
                "name": "Schedule source.pdf",
                "content_type": "application/pdf",
                "size": 321,
                "url": f"/api/files/{document['id']}",
                "created_at": reviews[2]["document"]["created_at"],
            }

            versions = await cli.get(
                f"/api/trials/{trial['id']}/versions",
                headers=headers(sponsor_a))
            assert versions.status_code == 200, versions.text
            assert [row["version"] for row in versions.json()] == [2, 1]
            assert all("visits" not in row for row in versions.json())
            denied = await cli.get(
                f"/api/trials/{trial['id']}/versions",
                headers=headers(sponsor_b))
            assert denied.status_code == 403

        stored_trial = await server.db.trials.find_one(
            {"id": trial["id"]}, {"_id": 0})
        assert stored_trial["schedule_version"] == 2
        assert await server.db.schedule_versions.count_documents(
            {"trial_id": trial["id"]}) == 2
    try:
        run(flow())
    finally:
        run(cleanup())


def test_schedule_can_be_shared_with_any_site_in_sponsor_portfolio():
    async def flow():
        sponsor_a, _, _, trial, foreign_trial, _, _, _ = await build_world()
        portfolio_pi = {
            "id": str(uuid.uuid4()),
            "email": f"portfolio-pi-{RUN_ID}@example.com",
            "full_name": "Dr Portfolio PI",
            "role": "pi",
            "organization": f"Portfolio Site {RUN_ID}",
            "status": "Active",
        }
        foreign_pi = {
            "id": str(uuid.uuid4()),
            "email": f"foreign-pi-{RUN_ID}@example.com",
            "full_name": "Dr Foreign PI",
            "role": "pi",
            "organization": f"Foreign Site {RUN_ID}",
            "status": "Active",
        }
        network_pi = {
            "id": str(uuid.uuid4()),
            "email": f"network-pi-{RUN_ID}@example.com",
            "full_name": "Dr Network PI",
            "role": "pi",
            "organization": f"Network Hospitals {RUN_ID}",
            "status": "Active",
        }
        network_pi_two = {
            "id": str(uuid.uuid4()),
            "email": f"network-pi-two-{RUN_ID}@example.com",
            "full_name": "Dr Second Network PI",
            "role": "pi",
            "organization": network_pi["organization"],
            "status": "Active",
        }
        directory_pi = {
            "id": str(uuid.uuid4()),
            "email": f"directory-pi-{RUN_ID}@example.com",
            "full_name": "Dr Directory PI",
            "role": "pi",
            "organization": f"Directory Hospital {RUN_ID}",
            "org_admin": True,
            "status": "Active",
        }
        directory_pi_two = {
            "id": str(uuid.uuid4()),
            "email": f"directory-pi-two-{RUN_ID}@example.com",
            "full_name": "Dr Second Directory PI",
            "role": "pi",
            "organization": directory_pi["organization"],
            "status": "Active",
        }
        directory_org = {
            "id": str(uuid.uuid4()),
            "name": directory_pi["organization"],
            "type": "site",
            "status": "active",
            "address": "42 Discovery Road",
            "city": "Delhi",
        }
        portfolio_trial = {
            "id": str(uuid.uuid4()),
            "title": "Second Portfolio Trial",
            "protocol_id": f"PORTFOLIO-{RUN_ID}",
            "sponsor_name": sponsor_a["organization"],
            "created_by": sponsor_a["id"],
            "created_at": server.now(),
            "status": "active",
        }
        patients = [
            {
                "id": str(uuid.uuid4()),
                "trial_id": portfolio_trial["id"],
                "subject_id": f"PORTFOLIO-SUBJ-{RUN_ID}",
                "pi_id": portfolio_pi["id"],
                "created_by": portfolio_pi["id"],
                "status": "active",
                "created_at": server.now(),
            },
            {
                "id": str(uuid.uuid4()),
                "trial_id": foreign_trial["id"],
                "subject_id": f"FOREIGN-SUBJ-{RUN_ID}",
                "pi_id": foreign_pi["id"],
                "created_by": foreign_pi["id"],
                "status": "active",
                "created_at": server.now(),
            },
        ]
        await server.db.users.insert_many([
            portfolio_pi, foreign_pi, network_pi, network_pi_two, directory_pi,
            directory_pi_two,
        ])
        await server.db.organizations.insert_one(directory_org)
        await server.db.trials.insert_one(portfolio_trial)
        await server.db.patients.insert_many(patients)
        organization = await server.db.organizations.find_one(
            {"name": sponsor_a["organization"]}, {"_id": 0, "id": 1})
        network_site = {
            "id": str(uuid.uuid4()),
            "org_id": organization["id"],
            "name": f"Network Hospital {RUN_ID}",
            "pi_name": network_pi["full_name"],
            "pi_email": network_pi["email"],
            "trial_ids": [],
        }
        await server.db.org_sites.insert_one(network_site)
        IDS["users"].extend([
            portfolio_pi["id"], foreign_pi["id"], network_pi["id"],
            network_pi_two["id"], directory_pi["id"], directory_pi_two["id"],
        ])
        IDS["trials"].append(portfolio_trial["id"])
        IDS["patients"].extend(patient["id"] for patient in patients)
        IDS["sites"].append(network_site["id"])

        async with client() as cli:
            directory = await cli.get(
                "/api/sponsor/share-site-directory",
                params={"trial_id": trial["id"]},
                headers=headers(sponsor_a),
            )
            assert directory.status_code == 200, directory.text
            directory_site = next(
                row for row in directory.json()
                if row["organization_id"] == directory_org["id"])
            assert directory_site["pi_id"] == directory_pi["id"]
            assert directory_site["pi_count"] == 2
            assert [pi["id"] for pi in directory_site["pis"]] == [
                directory_pi["id"], directory_pi_two["id"],
            ]
            assert directory_site["in_network"] is False
            assert directory_site["assigned_to_trial"] is False
            assert directory_site["can_receive_schedule"] is True

            dashboard = await cli.get(
                "/api/sponsor/dashboard", headers=headers(sponsor_a))
            assert dashboard.status_code == 200, dashboard.text
            dashboard_site = next(
                row for row in dashboard.json()["sites"]
                if row["id"] == network_site["id"])
            assert {pi["id"] for pi in dashboard_site["pis"]} == {
                network_pi["id"], network_pi_two["id"],
            }

            shared = await cli.post(
                "/api/shares",
                headers=headers(sponsor_a),
                json={
                    "trial_id": trial["id"],
                    "via": "in_app",
                    "sites": [{
                        "id": f"pi-{portfolio_pi['id']}",
                        "name": portfolio_pi["organization"],
                        "reviewer_id": portfolio_pi["id"],
                    }],
                },
            )
            assert shared.status_code == 200, shared.text

            network_shared = await cli.post(
                "/api/shares",
                headers=headers(sponsor_a),
                json={
                    "trial_id": trial["id"],
                    "via": "in_app",
                    "sites": [{
                        "id": network_site["id"],
                        "name": network_site["name"],
                        "reviewer_id": network_pi["id"],
                    }],
                },
            )
            assert network_shared.status_code == 200, network_shared.text

            spoofed_directory_share = await cli.post(
                "/api/shares",
                headers=headers(sponsor_a),
                json={
                    "trial_id": trial["id"],
                    "via": "in_app",
                    "sites": [{
                        "id": directory_site["id"],
                        "name": directory_site["name"],
                        "reviewer_id": foreign_pi["id"],
                        "organization_id": directory_org["id"],
                    }],
                },
            )
            assert spoofed_directory_share.status_code == 403

            directory_shared = await cli.post(
                "/api/shares",
                headers=headers(sponsor_a),
                json={
                    "trial_id": trial["id"],
                    "via": "in_app",
                    "sites": [{
                        "id": directory_site["id"],
                        "name": directory_site["name"],
                        "reviewer_id": directory_pi["id"],
                        "organization_id": directory_org["id"],
                    }],
                },
            )
            assert directory_shared.status_code == 200, directory_shared.text
            linked_directory_site = await server.db.org_sites.find_one({
                "org_id": organization["id"],
                "site_org_id": directory_org["id"],
            }, {"_id": 0})
            assert linked_directory_site is not None
            assert linked_directory_site["user_id"] == directory_pi["id"]
            assert trial["id"] in linked_directory_site["trial_ids"]
            assert directory_shared.json()["site_ids"] == [
                linked_directory_site["id"]]
            IDS["sites"].append(linked_directory_site["id"])
            pi_trials = await cli.get(
                "/api/trials", headers=headers(directory_pi))
            assert pi_trials.status_code == 200, pi_trials.text
            assert trial["id"] in {row["id"] for row in pi_trials.json()}

            second_directory_share = await cli.post(
                "/api/shares",
                headers=headers(sponsor_a),
                json={
                    "trial_id": trial["id"],
                    "via": "in_app",
                    "sites": [{
                        "id": linked_directory_site["id"],
                        "name": directory_site["name"],
                        "reviewer_id": directory_pi_two["id"],
                        "organization_id": directory_org["id"],
                    }],
                },
            )
            assert second_directory_share.status_code == 200, second_directory_share.text
            preserved_site = await server.db.org_sites.find_one(
                {"id": linked_directory_site["id"]}, {"_id": 0})
            assert preserved_site["user_id"] == directory_pi["id"]
            assert set(preserved_site["pi_ids"]) == {
                directory_pi["id"], directory_pi_two["id"],
            }
            assert await server.db.schedule_reviews.count_documents({
                "trial_id": trial["id"],
                "reviewer_id": directory_pi_two["id"],
            }) == 1

            denied = await cli.post(
                "/api/shares",
                headers=headers(sponsor_a),
                json={
                    "trial_id": trial["id"],
                    "via": "in_app",
                    "sites": [{
                        "id": f"pi-{foreign_pi['id']}",
                        "name": foreign_pi["organization"],
                        "reviewer_id": foreign_pi["id"],
                    }],
                },
            )
            assert denied.status_code == 403, denied.text

    try:
        run(flow())
    finally:
        run(cleanup())


async def cleanup():
    if IDS["trials"]:
        await server.db.schedule_reviews.delete_many(
            {"trial_id": {"$in": IDS["trials"]}})
        await server.db.schedule_versions.delete_many(
            {"trial_id": {"$in": IDS["trials"]}})
        await server.db.shares.delete_many(
            {"trial_id": {"$in": IDS["trials"]}})
        await server.db.notifications.delete_many(
            {"trial_id": {"$in": IDS["trials"]}})
        await server.db.audit_logs.delete_many(
            {"trial_id": {"$in": IDS["trials"]}})
        await server.db.invitations.delete_many(
            {"trial_id": {"$in": IDS["trials"]}})
        await server.db.trials.delete_many({"id": {"$in": IDS["trials"]}})
    if IDS["instances"]:
        await server.db.visit_instances.delete_many(
            {"id": {"$in": IDS["instances"]}})
    if IDS["patients"]:
        await server.db.patients.delete_many(
            {"id": {"$in": IDS["patients"]}})
    if IDS["visits"]:
        await server.db.visits.delete_many({"id": {"$in": IDS["visits"]}})
    if IDS["sites"]:
        await server.db.org_sites.delete_many({"id": {"$in": IDS["sites"]}})
    if IDS["files"]:
        await server.db.files.delete_many({"id": {"$in": IDS["files"]}})
    if IDS["users"]:
        users = await server.db.users.find(
            {"id": {"$in": IDS["users"]}}, {"_id": 0, "organization": 1}
        ).to_list(100)
        await server.db.organizations.delete_many({
            "name": {"$in": [row.get("organization") for row in users]}
        })
        await server.db.users.delete_many({"id": {"$in": IDS["users"]}})
    for values in IDS.values():
        values.clear()


def teardown_module():
    LOOP.close()

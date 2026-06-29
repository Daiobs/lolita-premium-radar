import json
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from lolita_radar.models import ItemStatus, RadarItem
from lolita_radar.storage import connect, diff_and_store
from lolita_radar.web import INDEX_HTML, get_dashboard_state, make_handler


class WebTests(unittest.TestCase):
    def test_dashboard_state_includes_sources_items_and_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "sources.yaml"
            db_path = root / "radar.sqlite"
            config_path.write_text(
                """
sources:
  metamorphose:
    type: metamorphose
    enabled: true
    url: "https://metamorphose.gr.jp/en/news"
    keywords:
      - "JSK"
      - "OP"
""".strip(),
                encoding="utf-8",
            )

            connection = connect(db_path)
            try:
                diff_and_store(
                    connection,
                    [
                        RadarItem(
                            source="metamorphose",
                            title="New Arrival: Rose JSK",
                            url="https://example.com/news/rose",
                            status=ItemStatus.NEW_ARRIVAL,
                        )
                    ],
                )
            finally:
                connection.close()

            state = get_dashboard_state(config_path=config_path, db_path=db_path)

            self.assertTrue(state["ok"])
            self.assertEqual(state["counts"]["sources"], 1)
            self.assertEqual(state["counts"]["items"], 1)
            self.assertEqual(state["counts"]["events"], 1)
            self.assertEqual(state["sources"][0]["name"], "metamorphose")
            self.assertEqual(state["brand_weights"][0]["alias"], "AP")
            self.assertEqual(state["brand_weights"][0]["weight"], 100)
            self.assertIn("brands_path", state)
            self.assertIn("market_path", state)
            self.assertIn("market", state)
            self.assertIn("patterns", state["market"])
            self.assertIn("momentum", state["market"])
            self.assertIn("sample_plan", state["market"])
            self.assertTrue(state["market"]["sample_plan"])
            self.assertIn("premium_bands", state["market"]["summary"])
            self.assertIn("opportunity_radar", state)
            self.assertIn("brand_weight_profile", state)
            self.assertIn("market_alerts", state)
            self.assertIn("summary", state["market_alerts"])
            self.assertTrue(state["market_alerts"]["alerts"])
            self.assertEqual(state["brand_weight_profile"][0]["alias"], "AP")
            self.assertIn("weight_role", state["brand_weight_profile"][0])
            self.assertIn("watch_urls", state["brand_weight_profile"][0])
            self.assertTrue(state["opportunity_radar"])
            self.assertTrue(state["focus_queue"])
            self.assertEqual(state["items"][0]["title"], "New Arrival: Rose JSK")
            self.assertEqual(state["events"][0]["event_type"], "new_item")

    def test_index_html_includes_language_switch(self) -> None:
        self.assertIn('data-language="zh"', INDEX_HTML)
        self.assertIn('data-language="en"', INDEX_HTML)
        self.assertIn("中文", INDEX_HTML)
        self.assertIn("Check All", INDEX_HTML)
        self.assertIn("brandWeights", INDEX_HTML)
        self.assertIn("hero-visual", INDEX_HTML)
        self.assertIn("/assets/lolita-radar-fabric.png", INDEX_HTML)
        self.assertIn("heroVisualTitle", INDEX_HTML)
        self.assertIn("heroVisualPremium", INDEX_HTML)
        self.assertIn("radar-nav", INDEX_HTML)
        self.assertIn("data-radar-jump", INDEX_HTML)
        self.assertIn("jumpToRadarSection", INDEX_HTML)
        self.assertIn("navIdentity", INDEX_HTML)
        self.assertIn("navFormula", INDEX_HTML)
        self.assertIn("marketSignal", INDEX_HTML)
        self.assertIn("focusQueue", INDEX_HTML)
        self.assertIn("marketAlertLine", INDEX_HTML)
        self.assertIn("renderMarketAlertLine", INDEX_HTML)
        self.assertIn("renderMarketAlert", INDEX_HTML)
        self.assertIn("alertReason", INDEX_HTML)
        self.assertIn("marketMomentum", INDEX_HTML)
        self.assertIn("renderMarketMomentum", INDEX_HTML)
        self.assertIn("momentumBar", INDEX_HTML)
        self.assertIn("momentumDirection", INDEX_HTML)
        self.assertIn("momentumRising", INDEX_HTML)
        self.assertIn("brandRadarMatrix", INDEX_HTML)
        self.assertIn("renderBrandRadarMatrix", INDEX_HTML)
        self.assertIn("buildBrandRadarMatrix", INDEX_HTML)
        self.assertIn("matrixAction", INDEX_HTML)
        self.assertIn("data-matrix-filter", INDEX_HTML)
        self.assertIn('data-matrix-filter="focus"', INDEX_HTML)
        self.assertIn("matrixFilterFocus", INDEX_HTML)
        self.assertIn("isFocusBrand", INDEX_HTML)
        self.assertIn("matrixSort", INDEX_HTML)
        self.assertIn("sortMatrixRows", INDEX_HTML)
        self.assertIn("filterMatrixRows", INDEX_HTML)
        self.assertIn("matrix-action", INDEX_HTML)
        self.assertIn("matrixActionReason", INDEX_HTML)
        self.assertIn("opportunityRadar", INDEX_HTML)
        self.assertIn("opportunitySummary", INDEX_HTML)
        self.assertIn("data-opportunity-filter", INDEX_HTML)
        self.assertIn("score-breakdown", INDEX_HTML)
        self.assertIn("premiumPoints", INDEX_HTML)
        self.assertIn("marketPremium", INDEX_HTML)
        self.assertIn("marketForm", INDEX_HTML)
        self.assertIn("premiumRecordFilters", INDEX_HTML)
        self.assertIn("data-premium-filter", INDEX_HTML)
        self.assertIn("premiumBrandFilter", INDEX_HTML)
        self.assertIn("syncPremiumBrandFilter", INDEX_HTML)
        self.assertIn("filterPremiumRecords", INDEX_HTML)
        self.assertIn("activePremiumBrandFilter", INDEX_HTML)
        self.assertIn("exportPremiumCsvBtn", INDEX_HTML)
        self.assertIn("exportPremiumCsv", INDEX_HTML)
        self.assertIn("csvFromPremiumRecords", INDEX_HTML)
        self.assertIn("premiumCsvFilename", INDEX_HTML)
        self.assertIn("csvCell", INDEX_HTML)
        self.assertIn("premiumBandCollector", INDEX_HTML)
        self.assertIn("premiumBandPill", INDEX_HTML)
        self.assertIn("premiumBandLabelKey", INDEX_HTML)
        self.assertIn("price-corridor", INDEX_HTML)
        self.assertIn("renderPriceCorridor", INDEX_HTML)
        self.assertIn("priceCorridorWidth", INDEX_HTML)
        self.assertIn("avg_spread", INDEX_HTML)
        self.assertIn("retailRange", INDEX_HTML)
        self.assertIn("resaleRange", INDEX_HTML)
        self.assertIn("samplePreview", INDEX_HTML)
        self.assertIn("renderSamplePreview", INDEX_HTML)
        self.assertIn("sampleSignalLabel", INDEX_HTML)
        self.assertIn("/api/market/observations", INDEX_HTML)
        self.assertIn("/api/brand-weights", INDEX_HTML)
        self.assertIn("saveWeightsBtn", INDEX_HTML)
        self.assertIn("resetWeightsBtn", INDEX_HTML)
        self.assertIn("exportWeightsCsvBtn", INDEX_HTML)
        self.assertIn("exportBrandWeightsCsv", INDEX_HTML)
        self.assertIn("csvFromBrandWeights", INDEX_HTML)
        self.assertIn("lolita-brand-weights.csv", INDEX_HTML)
        self.assertIn("weightDirtyStatus", INDEX_HTML)
        self.assertIn("weightDraftAudit", INDEX_HTML)
        self.assertIn("renderWeightDraftAudit", INDEX_HTML)
        self.assertIn("weightDraftRows", INDEX_HTML)
        self.assertIn("weightDraftStats", INDEX_HTML)
        self.assertIn("weightDraftRisks", INDEX_HTML)
        self.assertIn("weightDirtyStatusText", INDEX_HTML)
        self.assertIn("weight-draft-summary", INDEX_HTML)
        self.assertIn("weight-draft-warning", INDEX_HTML)
        self.assertIn("weightDraftAvgDelta", INDEX_HTML)
        self.assertIn("weightDraftMaxMove", INDEX_HTML)
        self.assertIn("weightDraftRiskCoreDown", INDEX_HTML)
        self.assertIn("weightDraftRiskThinRaise", INDEX_HTML)
        self.assertIn("weightsRisk", INDEX_HTML)
        self.assertIn("weight-draft-row", INDEX_HTML)
        self.assertIn("data-original-weight", INDEX_HTML)
        self.assertIn("brand-cameo", INDEX_HTML)
        self.assertIn("brand-ribbon", INDEX_HTML)
        self.assertIn("brand-keywords", INDEX_HTML)
        self.assertIn("brandStyleLedger", INDEX_HTML)
        self.assertIn("renderBrandStyleLedger", INDEX_HTML)
        self.assertIn("brandStyleLedgerRows", INDEX_HTML)
        self.assertIn("brandStyleFamily", INDEX_HTML)
        self.assertIn("style-ledger-card", INDEX_HTML)
        self.assertIn("styleFamilySweet", INDEX_HTML)
        self.assertIn("brandThemeClass", INDEX_HTML)
        self.assertIn("brandKeywordPearlsHtml", INDEX_HTML)
        self.assertIn("premiumSeedRadar", INDEX_HTML)
        self.assertIn("renderPremiumSeedRadar", INDEX_HTML)
        self.assertIn("premiumSeedRows", INDEX_HTML)
        self.assertIn("premiumSeedIntentKey", INDEX_HTML)
        self.assertIn("premiumSeedSummary", INDEX_HTML)
        self.assertIn("renderPremiumSeedSummary", INDEX_HTML)
        self.assertIn("premiumSeedStats", INDEX_HTML)
        self.assertIn("premiumSeedTaskCount", INDEX_HTML)
        self.assertIn("premiumSeedStage", INDEX_HTML)
        self.assertIn("premiumSeedStageLabel", INDEX_HTML)
        self.assertIn("premiumSeedStagePill", INDEX_HTML)
        self.assertIn("premiumSeedStageSeed", INDEX_HTML)
        self.assertIn("data-premium-seed-keyword", INDEX_HTML)
        self.assertIn("premiumSeedIntentCoreGap", INDEX_HTML)
        self.assertIn("exportPremiumSeedsCsvBtn", INDEX_HTML)
        self.assertIn("exportPremiumSeedsCsv", INDEX_HTML)
        self.assertIn("csvFromPremiumSeedRows", INDEX_HTML)
        self.assertIn("lolita-premium-seeds.csv", INDEX_HTML)
        self.assertIn("theme-gothic", INDEX_HTML)
        self.assertIn("data-lolita-theme", INDEX_HTML)
        self.assertIn("theme-switch", INDEX_HTML)
        self.assertIn("data-theme-control", INDEX_HTML)
        self.assertIn("radarTheme", INDEX_HTML)
        self.assertIn("applyTheme", INDEX_HTML)
        self.assertIn("themeChanged", INDEX_HTML)
        self.assertIn("brand-identity", INDEX_HTML)
        self.assertIn("brandVisualStyle", INDEX_HTML)
        self.assertIn("brandIdentityHtml", INDEX_HTML)
        self.assertIn("visualMotif", INDEX_HTML)
        self.assertIn("cssHexColor", INDEX_HTML)
        self.assertIn("weight-insight", INDEX_HTML)
        self.assertIn("brandWeightInsightHtml", INDEX_HTML)
        self.assertIn("weightBandCore", INDEX_HTML)
        self.assertIn("weightIntentArchive", INDEX_HTML)
        self.assertIn("weightScenarios", INDEX_HTML)
        self.assertIn("data-weight-scenario", INDEX_HTML)
        self.assertIn("applyWeightScenario", INDEX_HTML)
        self.assertIn("scenarioTargetWeight", INDEX_HTML)
        self.assertIn("roundWeightStep", INDEX_HTML)
        self.assertIn("scenarioRelease", INDEX_HTML)
        self.assertIn("scenarioPremium", INDEX_HTML)
        self.assertIn("scenarioEvidence", INDEX_HTML)
        self.assertIn("weightSnapshot", INDEX_HTML)
        self.assertIn("renderWeightSnapshot", INDEX_HTML)
        self.assertIn("weightSnapshotStats", INDEX_HTML)
        self.assertIn("brandWeightStrategy", INDEX_HTML)
        self.assertIn("renderBrandWeightStrategy", INDEX_HTML)
        self.assertIn("weightStrategyStats", INDEX_HTML)
        self.assertIn("brandWeightStrategyMoves", INDEX_HTML)
        self.assertIn("strategyPill", INDEX_HTML)
        self.assertIn("strategyReasonCoreGap", INDEX_HTML)
        self.assertIn("weightTrajectory", INDEX_HTML)
        self.assertIn("renderWeightTrajectory", INDEX_HTML)
        self.assertIn("buildWeightTrajectory", INDEX_HTML)
        self.assertIn("weightTrajectoryStats", INDEX_HTML)
        self.assertIn("trajectoryPill", INDEX_HTML)
        self.assertIn("data-trajectory-apply", INDEX_HTML)
        self.assertIn("data-trajectory-sample", INDEX_HTML)
        self.assertIn("brandWeightFormula", INDEX_HTML)
        self.assertIn("renderBrandWeightFormula", INDEX_HTML)
        self.assertIn("buildBrandWeightFormula", INDEX_HTML)
        self.assertIn("brandWeightFormulaParts", INDEX_HTML)
        self.assertIn("formulaConfidence", INDEX_HTML)
        self.assertIn("data-formula-apply", INDEX_HTML)
        self.assertIn("applyFormulaDraft", INDEX_HTML)
        self.assertIn("formula_target", INDEX_HTML)
        self.assertIn("brandWeightProfile", INDEX_HTML)
        self.assertIn("renderBrandWeightProfile", INDEX_HTML)
        self.assertIn("profileBar", INDEX_HTML)
        self.assertIn("brandIdentityMatrix", INDEX_HTML)
        self.assertIn("renderBrandIdentityMatrix", INDEX_HTML)
        self.assertIn("brandIdentityCardHtml", INDEX_HTML)
        self.assertIn("brandIdentityStats", INDEX_HTML)
        self.assertIn("identity-card", INDEX_HTML)
        self.assertIn("identity-swatch", INDEX_HTML)
        self.assertIn("identity-links", INDEX_HTML)
        self.assertIn("brandWatchLinksHtml", INDEX_HTML)
        self.assertIn("watch_urls", INDEX_HTML)
        self.assertIn("safeUrl", INDEX_HTML)
        self.assertIn("--ribbon-shadow", INDEX_HTML)
        self.assertIn("weightRole", INDEX_HTML)
        self.assertIn("evidenceLevel", INDEX_HTML)
        self.assertIn("data-weight-sample", INDEX_HTML)
        self.assertIn("brandKeywordRadar", INDEX_HTML)
        self.assertIn("renderBrandKeywordRadar", INDEX_HTML)
        self.assertIn("data-keyword-brand", INDEX_HTML)
        self.assertIn("prepareKeywordSample", INDEX_HTML)
        self.assertIn("patternPremiumRadar", INDEX_HTML)
        self.assertIn("renderPatternPremiumRadar", INDEX_HTML)
        self.assertIn("renderPatternEvidence", INDEX_HTML)
        self.assertIn("data-pattern-brand", INDEX_HTML)
        self.assertIn("marketActionDesk", INDEX_HTML)
        self.assertIn("renderMarketActionDesk", INDEX_HTML)
        self.assertIn("marketSearchLinks", INDEX_HTML)
        self.assertIn("data-action-sample", INDEX_HTML)
        self.assertIn("marketUrl", INDEX_HTML)
        self.assertIn("marketNotes", INDEX_HTML)
        self.assertIn("evidenceHealth", INDEX_HTML)
        self.assertIn("renderEvidenceHealth", INDEX_HTML)
        self.assertIn("qualityScore", INDEX_HTML)
        self.assertIn("weightTuning", INDEX_HTML)
        self.assertIn("buildWeightTuning", INDEX_HTML)
        self.assertIn("tuningSuggestion", INDEX_HTML)
        self.assertIn("applyTuningBatchBtn", INDEX_HTML)
        self.assertIn("applyAllTuningDrafts", INDEX_HTML)
        self.assertIn("syncTuningBatchControls", INDEX_HTML)
        self.assertIn("actionableTuningSuggestions", INDEX_HTML)
        self.assertIn("updateWeightDraftInput", INDEX_HTML)
        self.assertIn("tuningCollectReason", INDEX_HTML)
        self.assertIn("sampleCoverage", INDEX_HTML)
        self.assertIn("renderSampleCoverage", INDEX_HTML)
        self.assertIn("data-coverage-sample", INDEX_HTML)
        self.assertIn("samplePlan", INDEX_HTML)
        self.assertIn("renderSamplePlan", INDEX_HTML)
        self.assertIn("buildSamplePlanRows", INDEX_HTML)
        self.assertIn("samplePlanSummaryHtml", INDEX_HTML)
        self.assertIn("samplePlanStats", INDEX_HTML)
        self.assertIn("sample-plan-summary", INDEX_HTML)
        self.assertIn("samplePlanCompletion", INDEX_HTML)
        self.assertIn("exportSamplePlanCsvBtn", INDEX_HTML)
        self.assertIn("exportSamplePlanCsv", INDEX_HTML)
        self.assertIn("csvFromSamplePlanRows", INDEX_HTML)
        self.assertIn("lolita-sample-plan.csv", INDEX_HTML)
        self.assertIn("data-sample-plan", INDEX_HTML)
        self.assertIn("data-sample-plan-keyword", INDEX_HTML)
        self.assertIn("data-sample-plan-keyword-brand", INDEX_HTML)
        self.assertIn("samplePlanSeed", INDEX_HTML)
        self.assertIn("tuningActionHtml", INDEX_HTML)
        self.assertIn("data-tuning-sample", INDEX_HTML)
        self.assertIn("applyTuningDraft", INDEX_HTML)
        self.assertIn("prepareMarketSample", INDEX_HTML)
        self.assertIn("normalizeAlias", INDEX_HTML)
        self.assertIn("draftPreview", INDEX_HTML)
        self.assertIn("buildDraftOpportunityRadar", INDEX_HTML)
        self.assertIn("scoreDelta", INDEX_HTML)
        self.assertIn("formatDelta", INDEX_HTML)
        self.assertIn("hasScoreDelta", INDEX_HTML)
        self.assertIn("opportunityPriorityScore", INDEX_HTML)
        self.assertIn("priorityScore", INDEX_HTML)

    def test_static_visual_asset_is_served(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "sources.yaml"
            db_path = root / "radar.sqlite"
            config_path.write_text(
                """
sources:
  metamorphose:
    type: metamorphose
    enabled: true
    url: "https://metamorphose.gr.jp/en/news"
""".strip(),
                encoding="utf-8",
            )

            handler = make_handler(config_path=config_path, db_path=db_path)
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/assets/lolita-radar-fabric.png"
                with urllib.request.urlopen(url) as response:
                    body = response.read(8)

                self.assertEqual(response.headers.get_content_type(), "image/png")
                self.assertEqual(body, b"\x89PNG\r\n\x1a\n")
            finally:
                server.shutdown()
                server.server_close()

    def test_market_observation_post_appends_sample(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "sources.yaml"
            db_path = root / "radar.sqlite"
            market_path = root / "market.json"
            config_path.write_text(
                """
sources:
  metamorphose:
    type: metamorphose
    enabled: true
    url: "https://metamorphose.gr.jp/en/news"
""".strip(),
                encoding="utf-8",
            )
            market_path.write_text("[]\n", encoding="utf-8")

            handler = make_handler(config_path=config_path, db_path=db_path, market_path=market_path)
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/api/market/observations"
                request = urllib.request.Request(
                    url,
                    data=json.dumps(
                        {
                            "brand_alias": "AP",
                            "item_name": "Rose JSK",
                            "retail_price": 2000,
                            "resale_price": 3000,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request) as response:
                    payload = json.loads(response.read().decode("utf-8"))

                self.assertEqual(payload["added_market_observation"]["premium_rate"], 0.5)
                self.assertEqual(payload["market"]["summary"]["sample_count"], 1)
                self.assertEqual(payload["market"]["summary"]["brands"][0]["brand_alias"], "AP")
            finally:
                server.shutdown()
                server.server_close()

    def test_brand_weights_put_updates_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "sources.yaml"
            db_path = root / "radar.sqlite"
            brands_path = root / "brands.json"
            config_path.write_text(
                """
sources:
  metamorphose:
    type: metamorphose
    enabled: true
    url: "https://metamorphose.gr.jp/en/news"
""".strip(),
                encoding="utf-8",
            )
            brands_path.write_text(
                json.dumps(
                    [
                        {"name": "Angelic Pretty", "alias": "AP", "weight": 100, "tier": "core", "keywords": ["angelic pretty"]},
                        {"name": "Metamorphose", "alias": "Meta", "weight": 80, "tier": "watch", "keywords": ["metamorphose"]},
                    ]
                ),
                encoding="utf-8",
            )

            handler = make_handler(config_path=config_path, db_path=db_path, brands_path=brands_path)
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/api/brand-weights"
                request = urllib.request.Request(
                    url,
                    data=json.dumps({"weights": [{"alias": "Meta", "weight": 96}]}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="PUT",
                )
                with urllib.request.urlopen(request) as response:
                    payload = json.loads(response.read().decode("utf-8"))

                meta = next(brand for brand in payload["brand_weights"] if brand["alias"] == "Meta")
                self.assertEqual(meta["weight"], 96)
                saved = json.loads(brands_path.read_text(encoding="utf-8"))
                self.assertEqual(next(brand for brand in saved if brand["alias"] == "Meta")["weight"], 96)
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()

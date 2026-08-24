from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from workspace.ai.services.reading import (
    CHUNK_MAX_CHARS,
    MAX_CHUNKS,
    MAX_PARALLEL_READS,
    Extraction,
    Finding,
    read_for_query,
)

_INTRO = "Everything the service exposes is documented on this single page."
_ANSWER = (
    "The API answers a 429 status once the cap is exceeded, and names the "
    "delay to wait in a Retry-After header."
)


def _filler(topic: str, count: int) -> list[str]:
    return [
        f"{topic} note {i}: {topic} behaviour spelled out at the length "
        "documentation is written at, so the page is long."
        for i in range(count)
    ]


def _reference_page() -> str:
    return "\n\n".join(
        [
            "# Widget API reference",
            _INTRO,
            "## Authentication",
            *_filler("Token", 20),
            "## Rate limiting",
            _ANSWER,
            "## Webhooks",
            *_filler("Callback", 20),
        ]
    )


def _answers(*items, missing=""):
    """A model reporting *items* for every chunk it is given.

    An item is the text of a finding, or a ``(section, text)`` pair when the
    section it came from matters to the test.
    """
    findings = [
        Finding(section=item[0], text=item[1])
        if isinstance(item, tuple)
        else Finding(text=item)
        for item in items
    ]
    return lambda *a, **kw: (Extraction(findings=findings, missing=missing), {})


@override_settings(
    AI_READING_MODEL="", AI_SMALL_MODEL="small-model", AI_MODEL="big-model"
)
class ReadForQueryTests(SimpleTestCase):
    def setUp(self):
        self.page = _reference_page()

    def _read(self, side_effect, *, query="what happens at the cap?", max_chars=1500):
        with patch(
            "workspace.ai.services.reading.call_llm_structured",
            side_effect=side_effect,
        ) as mock_call:
            text = read_for_query(self.page, query, max_chars=max_chars)
        return text, mock_call

    def test_what_the_model_reports_is_what_the_reader_gets(self):
        condensed = "429 once the cap is exceeded; Retry-After names the delay."

        text, _ = self._read(_answers(condensed))

        self.assertIn(condensed, text)
        self.assertNotIn("Token note 0", text)

    def test_the_lead_names_the_query(self):
        text, _ = self._read(_answers(_ANSWER), query="what happens at the cap?")

        self.assertIn('Read for "what happens at the cap?"', text)

    def test_a_finding_is_rendered_under_the_section_it_came_from(self):
        text, _ = self._read(_answers(("Rate limiting", _ANSWER)))

        self.assertIn("### Rate limiting", text)
        self.assertLess(text.index("### Rate limiting"), text.index(_ANSWER))

    def test_findings_sharing_a_section_are_labelled_once(self):
        text, _ = self._read(
            _answers(("Rate limiting", _ANSWER), ("Rate limiting", "Bursts of 60."))
        )

        self.assertEqual(text.count("### Rate limiting"), 1)
        self.assertIn("Bursts of 60.", text)

    def test_a_section_named_again_after_another_is_labelled_again(self):
        text, _ = self._read(
            _answers(
                ("Rate limiting", _ANSWER),
                ("Webhooks", "Callbacks retry three times."),
                ("Rate limiting", "Bursts of 60."),
            )
        )

        self.assertEqual(text.count("### Rate limiting"), 2)

    def test_a_finding_without_a_section_carries_no_label(self):
        text, _ = self._read(_answers(_ANSWER))

        self.assertNotIn("###", text)
        self.assertIn(_ANSWER, text)

    def test_findings_keep_the_order_the_model_reported_them(self):
        text, _ = self._read(_answers(_INTRO, _ANSWER))

        self.assertLess(text.index(_INTRO), text.index(_ANSWER))

    def test_the_outline_is_built_without_the_model(self):
        text, _ = self._read(_answers(_ANSWER))

        self.assertIn("## Page outline", text)
        self.assertIn("- Widget API reference", text)
        self.assertIn("  - Rate limiting", text)
        self.assertIn("  - Webhooks", text)

    def test_the_missing_note_is_reported(self):
        text, _ = self._read(
            _answers(_ANSWER, missing="It never says how long a ban lasts.")
        )

        self.assertIn("Not on this page: It never says how long a ban lasts.", text)

    def test_no_missing_note_when_the_model_had_none(self):
        text, _ = self._read(_answers(_ANSWER))

        self.assertNotIn("Not on this page:", text)

    def test_the_result_stays_within_the_budget(self):
        text, _ = self._read(_answers(*_filler("Token", 20)), max_chars=900)

        self.assertLessEqual(len(text), 900)

    def test_a_finding_larger_than_the_budget_is_cut_not_abandoned(self):
        long_answer = "\n\n".join(_filler("Token", 20))
        page = f"# Manual\n\n{long_answer}\n\n" + "\n\n".join(_filler("Callback", 40))

        with patch(
            "workspace.ai.services.reading.call_llm_structured",
            side_effect=_answers(long_answer),
        ):
            text = read_for_query(page, "tokens", max_chars=700)

        self.assertLessEqual(len(text), 700)
        self.assertIn("Token note 0", text)

    def test_a_query_in_another_language_reaches_the_model_unchanged(self):
        query = "que se passe-t-il quand la limite est atteinte ?"

        _, mock_call = self._read(_answers(_ANSWER), query=query)

        prompt = mock_call.call_args.args[0][1]["content"]
        self.assertIn(query, prompt)

    def test_the_model_is_the_small_one(self):
        _, mock_call = self._read(_answers(_ANSWER))

        self.assertEqual(mock_call.call_args.kwargs["model"], "small-model")

    @override_settings(AI_READING_MODEL="reading-model")
    def test_the_reading_model_wins_over_the_small_one(self):
        _, mock_call = self._read(_answers(_ANSWER))

        self.assertEqual(mock_call.call_args.kwargs["model"], "reading-model")

    @override_settings(AI_READING_MODEL="reading-model", AI_SMALL_MODEL="")
    def test_the_reading_model_stands_alone(self):
        _, mock_call = self._read(_answers(_ANSWER))

        self.assertEqual(mock_call.call_args.kwargs["model"], "reading-model")

    @override_settings(AI_SMALL_MODEL="", AI_MODEL="big-model")
    def test_the_main_model_is_used_when_there_is_no_small_one(self):
        _, mock_call = self._read(_answers(_ANSWER))

        self.assertEqual(mock_call.call_args.kwargs["model"], "big-model")


@override_settings(AI_SMALL_MODEL="small-model", AI_MODEL="big-model")
class ReadForQueryShortcutTests(SimpleTestCase):
    """Cases answered without ever calling a model."""

    def _read_without_a_model_call(self, markdown, query, *, max_chars):
        with patch("workspace.ai.services.reading.call_llm_structured") as mock_call:
            text = read_for_query(markdown, query, max_chars=max_chars)
        mock_call.assert_not_called()
        return text

    def test_a_document_that_fits_is_returned_untouched(self):
        page = _reference_page()

        self.assertEqual(
            self._read_without_a_model_call(page, "the cap", max_chars=len(page)),
            page,
        )

    def test_no_query_reads_nothing(self):
        page = _reference_page()

        self.assertEqual(
            self._read_without_a_model_call(page, "   ", max_chars=500), page
        )

    def test_an_empty_document_is_returned_untouched(self):
        self.assertEqual(
            self._read_without_a_model_call("", "the cap", max_chars=0), ""
        )


@override_settings(AI_SMALL_MODEL="small-model", AI_MODEL="big-model")
class ReadForQueryFallbackTests(SimpleTestCase):
    """Every way the extraction can fail leaves the page as it was."""

    def setUp(self):
        self.page = _reference_page()

    def _read(self, side_effect):
        with patch(
            "workspace.ai.services.reading.call_llm_structured",
            side_effect=side_effect,
        ):
            return read_for_query(self.page, "the cap", max_chars=1500)

    def test_an_llm_exception_falls_back(self):
        self.assertEqual(self._read(RuntimeError("AI is not configured")), self.page)

    def test_a_malformed_result_falls_back(self):
        self.assertEqual(self._read(lambda *a, **kw: (None, {})), self.page)

    def test_an_empty_extraction_falls_back(self):
        self.assertEqual(self._read(_answers()), self.page)

    def test_findings_with_nothing_in_them_fall_back(self):
        self.assertEqual(self._read(_answers("", "   ")), self.page)

    @override_settings(AI_READING_MODEL="", AI_SMALL_MODEL="", AI_MODEL="")
    def test_no_model_configured_falls_back(self):
        with patch("workspace.ai.services.reading.call_llm_structured") as mock_call:
            text = read_for_query(self.page, "the cap", max_chars=1500)

        mock_call.assert_not_called()
        self.assertEqual(text, self.page)


@override_settings(
    AI_READING_MODEL="", AI_SMALL_MODEL="small-model", AI_MODEL="big-model"
)
class ReadForQueryChunkingTests(SimpleTestCase):
    def _page(self, blocks: int) -> str:
        # Each block is a fifth of a chunk, so the count of blocks sets the
        # count of chunks the document needs.
        body = "x" * (CHUNK_MAX_CHARS // 5)
        return "\n\n".join(f"Block {i}: {body}" for i in range(blocks))

    def _read(self, page, side_effect=None, *, max_chars=2000, part=1):
        with patch(
            "workspace.ai.services.reading.call_llm_structured",
            side_effect=side_effect or _answers("Block 0"),
        ) as mock_call:
            text = read_for_query(page, "blocks", max_chars=max_chars, part=part)
        return text, mock_call

    def _blocks_sent(self, mock_call):
        return "".join(call.args[0][1]["content"] for call in mock_call.call_args_list)

    def test_a_long_document_is_split_into_chunks(self):
        _, mock_call = self._read(self._page(12))

        self.assertGreater(mock_call.call_count, 1)

    def test_no_chunk_exceeds_the_prompt_size(self):
        _, mock_call = self._read(self._page(12))

        for call in mock_call.call_args_list:
            document = call.args[0][1]["content"]
            self.assertLessEqual(len(document), CHUNK_MAX_CHARS + 200)

    def test_every_chunk_of_a_short_enough_document_is_sent(self):
        page = self._page(12)

        _, mock_call = self._read(page)

        sent = "".join(call.args[0][1]["content"] for call in mock_call.call_args_list)
        for i in range(12):
            self.assertIn(f"Block {i}:", sent)

    def test_the_chunk_cap_is_enforced_and_logged(self):
        page = self._page(5 * (MAX_CHUNKS + 4))

        with self.assertLogs("workspace.ai.services.reading", level="INFO") as logs:
            _, mock_call = self._read(page)

        self.assertEqual(mock_call.call_count, MAX_CHUNKS)
        self.assertTrue(
            any("Page too long to read whole" in line for line in logs.output)
        )

    def test_a_page_read_in_part_says_so_and_how_to_read_on(self):
        page = self._page(5 * (MAX_CHUNKS + 4))

        with self.assertLogs("workspace.ai.services.reading", level="INFO"):
            text, _ = self._read(page, max_chars=4000)

        self.assertIn("Part 1 of 2", text)
        self.assertIn(f"of its {len(page)} characters", text)
        self.assertIn("part=2 for the next stretch of the page", text)

    def test_the_next_part_reads_the_chunks_the_first_one_left(self):
        page = self._page(5 * (MAX_CHUNKS + 4))

        with self.assertLogs("workspace.ai.services.reading", level="INFO"):
            _, first = self._read(page)
            text, second = self._read(page, part=2)

        self.assertNotEqual(self._blocks_sent(first), self._blocks_sent(second))
        self.assertIn("Block 0:", self._blocks_sent(first))
        self.assertNotIn("Block 0:", self._blocks_sent(second))
        self.assertIn("Block 99:", self._blocks_sent(second))
        self.assertIn("Part 2 of 2", text)
        self.assertIn("This is its last part.", text)

    def test_a_part_the_page_does_not_have_is_refused(self):
        with self.assertRaises(ValueError) as ctx:
            self._read(self._page(5 * (MAX_CHUNKS + 4)), part=3)

        self.assertIn("there is no part 3", str(ctx.exception))

    def test_a_page_that_fits_has_a_single_part(self):
        with self.assertRaises(ValueError) as ctx:
            self._read(self._page(2), max_chars=1_000_000, part=2)

        self.assertIn("This page has 1 part", str(ctx.exception))

    def test_a_partial_read_that_found_nothing_returns_the_part_it_covered(self):
        page = self._page(5 * (MAX_CHUNKS + 4))

        with self.assertLogs("workspace.ai.services.reading", level="INFO"):
            text, _ = self._read(page, side_effect=_answers(), part=2, max_chars=4000)

        self.assertIn("Part 2 of 2", text)
        self.assertIn("Block 99:", text)
        self.assertNotIn("Block 0:", text)

    @override_settings(AI_READING_MODEL="", AI_SMALL_MODEL="", AI_MODEL="")
    def test_a_read_with_no_model_falls_back_to_the_part_asked_for(self):
        page = self._page(5 * (MAX_CHUNKS + 4))

        text, mock_call = self._read(page, part=2)

        mock_call.assert_not_called()
        self.assertIn("Block 99:", text)
        self.assertNotIn("Block 0:", text)

    def test_a_page_read_whole_says_nothing_about_a_cut(self):
        text, _ = self._read(self._page(MAX_CHUNKS), max_chars=4000)

        self.assertNotIn("longer than one read", text)

    def test_the_fan_out_is_capped(self):
        page = self._page(5 * (MAX_CHUNKS + 4))

        with (
            self.assertLogs("workspace.ai.services.reading", level="INFO"),
            patch("workspace.ai.services.reading.ThreadPoolExecutor") as pool,
        ):
            self._read(page)

        self.assertLessEqual(pool.call_args.kwargs["max_workers"], MAX_PARALLEL_READS)

    def test_a_chunk_that_fails_does_not_sink_the_others(self):
        page = self._page(12)
        calls = []

        def flaky(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("backend down")
            return Extraction(findings=[Finding(text="Block 5")], missing=""), {}

        text, _ = self._read(page, side_effect=flaky)

        self.assertIn("Block 5", text)
        self.assertNotEqual(text, page)

    def test_a_failed_chunk_suppresses_the_gap_note(self):
        page = self._page(12)
        calls = []

        def flaky(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("backend down")
            return (
                Extraction(
                    findings=[Finding(text="Block 5")],
                    missing="Nothing here about blocks.",
                ),
                {},
            )

        text, _ = self._read(page, side_effect=flaky)

        self.assertIn("Block 5", text)
        self.assertNotIn("Not on this page:", text)

    def test_the_gap_note_needs_every_chunk_to_agree(self):
        page = self._page(12)
        calls = []

        def partial(*args, **kwargs):
            calls.append(1)
            missing = "Nothing here about blocks." if len(calls) > 1 else ""
            return Extraction(findings=[Finding(text="Block 0")], missing=missing), {}

        text, _ = self._read(page, side_effect=partial)

        self.assertNotIn("Not on this page:", text)

from zeitwerkzeug import PersonaParser, StandardWorker


def test_morning_block_starts_after_wake(
    utc_parser: PersonaParser,
    reference_utc,
) -> None:
    block = utc_parser.parse("morning", reference_utc)
    assert block.start > reference_utc
    assert block.end > block.start


def test_middle_third_block_is_inside_awake_window(
    utc_worker: StandardWorker,
    utc_parser: PersonaParser,
    reference_utc,
) -> None:
    block = utc_parser.parse("middle third of the day", reference_utc)
    awake = utc_worker.awake_block(reference_utc).to_utc()
    assert block.start >= awake.start
    assert block.end <= awake.end
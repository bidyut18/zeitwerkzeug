from zeitwerkzeug import FuzzyCron


def test_registry_can_add_and_get_job(
    fuzzy_cron: FuzzyCron,
    utc_noon_trigger,
    noop_job,
) -> None:
    registered = fuzzy_cron.register(noop_job, utc_noon_trigger, name="test-job")
    assert fuzzy_cron.get_job(registered.id) is registered


def test_registry_can_remove_job(
    fuzzy_cron: FuzzyCron,
    utc_noon_trigger,
    noop_job,
) -> None:
    registered = fuzzy_cron.register(noop_job, utc_noon_trigger, name="test-job")
    fuzzy_cron.remove(registered.id)
    assert fuzzy_cron.get_job(registered.id) is None
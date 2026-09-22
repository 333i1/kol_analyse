from app.services.worker import _validate_result_doc


def test_validate_result_doc_missing_jsonschema_is_module_not_found(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "jsonschema" or name.startswith("jsonschema."):
            raise ModuleNotFoundError("jsonschema")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    try:
        _validate_result_doc({"schema_version": "1.0.0"})
        raise AssertionError("expected ModuleNotFoundError")
    except ModuleNotFoundError as exc:
        assert "jsonschema" in str(exc)


def test_validate_retry_does_not_unboundlocalerror(monkeypatch):
    """Regression: import-inside-try left jsonschema unbound in except."""
    from app.services import worker

    calls = {"n": 0}

    def fake_validate(result):
        calls["n"] += 1
        if calls["n"] == 1:
            raise worker.json.decoder.JSONDecodeError("x", "x", 0) if False else ValueError("bad schema")
        return None

    monkeypatch.setattr(worker, "_validate_result_doc", fake_validate)
    monkeypatch.setattr(worker, "_repair_result", lambda result, err=None: result)
    monkeypatch.setattr(
        worker,
        "_degrade_to_failed_result",
        lambda result, err: result,
    )

    result = {"health": {"overall_level": "ok"}}
    schema_err = None
    valid = True
    try:
        worker._validate_result_doc(result)
    except Exception as e:
        schema_err = worker._format_schema_error(e)
        result = worker._repair_result(result, schema_err)
        try:
            worker._validate_result_doc(result)
            valid = True
        except Exception as e2:
            schema_err = worker._format_schema_error(e2)
            result = worker._degrade_to_failed_result(result, schema_err)
            try:
                worker._validate_result_doc(result)
                valid = True
            except Exception as e3:
                valid = False
                schema_err = worker._format_schema_error(e3)

    assert "UnboundLocalError" not in (schema_err or "")
    assert valid is True
    assert calls["n"] == 2

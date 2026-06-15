from agent.serializer import SafeSerializer


class BadRepr:
    def __repr__(self) -> str:
        raise RuntimeError("repr failed")


class InfiniteRepr:
    def __repr__(self) -> str:
        return repr(self)


class BadKey:
    def __str__(self) -> str:
        raise RuntimeError("no str")


def test_serializes_primitives():
    serializer = SafeSerializer()
    assert serializer.serialize(42) == 42
    assert serializer.serialize("hello") == "hello"
    assert serializer.serialize(None) is None
    assert serializer.serialize(True) is True


def test_serializes_bytes_with_length_and_preview():
    serializer = SafeSerializer()
    result = serializer.serialize(b"abc")
    assert result == "<bytes len=3 b'abc'>"


def test_serializes_callable_without_raising():
    serializer = SafeSerializer()

    def helper():
        return 1

    result = serializer.serialize(helper)
    assert result.startswith("<callable ")
    assert "helper" in result


def test_serializes_generator_without_raising():
    serializer = SafeSerializer()

    def gen():
        yield 1

    result = serializer.serialize(gen())
    assert result == "<generator>"


def test_serializes_circular_dict():
    serializer = SafeSerializer()
    circular: dict[str, object] = {"a": 1}
    circular["self"] = circular

    result = serializer.serialize(circular)
    assert result["a"] == 1
    assert result["self"] == "<circular>"


def test_depth_limit_truncates_nested_structures():
    serializer = SafeSerializer(max_depth=2)
    nested = {"l1": {"l2": {"l3": "deep"}}}

    result = serializer.serialize(nested)
    assert result["l1"]["l2"]["l3"] == "<max_depth>"


def test_serialize_locals_never_raises_on_pathological_values():
    serializer = SafeSerializer()
    locals_dict = {
        "num": 1,
        "fn": (lambda: None),
        "bad": BadRepr(),
    }
    result = serializer.serialize_locals(locals_dict)
    assert result["num"] == 1
    assert result["fn"].startswith("<callable")
    assert "BadRepr" in result["bad"]


def test_serializes_circular_list():
    serializer = SafeSerializer()
    circular: list[object] = []
    circular.append(circular)
    result = serializer.serialize(circular)
    assert result[0] == "<circular>"


def test_serializes_mutual_dict_cycle():
    serializer = SafeSerializer()
    left: dict[str, object] = {}
    right: dict[str, object] = {"peer": left}
    left["peer"] = right
    result = serializer.serialize(left)
    assert isinstance(result["peer"], dict)
    assert result["peer"]["peer"] == "<circular>"


def test_serializes_infinite_repr_without_raising():
    serializer = SafeSerializer()
    result = serializer.serialize(InfiniteRepr())
    assert "InfiniteRepr" in result


def test_serializes_dict_with_bad_key_without_raising():
    serializer = SafeSerializer()
    result = serializer.serialize({BadKey(): "value"})
    assert "<bad_key BadKey>" in result
    assert result["<bad_key BadKey>"] == "value"


def test_serializes_long_bytes_with_ellipsis():
    serializer = SafeSerializer()
    payload = b"x" * 40
    result = serializer.serialize(payload)
    assert result.startswith("<bytes len=40")
    assert "..." in result


def test_serializes_bytearray():
    serializer = SafeSerializer()
    assert serializer.serialize(bytearray(b"abc")) == "<bytearray len=3>"


def test_serialize_locals_survives_mixed_pathological_basket():
    serializer = SafeSerializer()
    circular: list[object] = []
    circular.append(circular)
    basket = {
        "circular_list": circular,
        "bad_repr": InfiniteRepr(),
        "nested": {"fn": (lambda: None), "bytes": b"\x00" * 64},
    }
    result = serializer.serialize_locals(basket)
    assert isinstance(result, dict)
    assert "serialization_error" not in result
    assert result["circular_list"][0] == "<circular>"
    assert result["nested"]["bytes"].startswith("<bytes len=64")


def test_serialize_never_raises_on_pathological_inputs():
    serializer = SafeSerializer()
    circular: list[object] = []
    circular.append(circular)
    left: dict[str, object] = {}
    right = {"peer": left}
    left["peer"] = right
    samples = [
        circular,
        left,
        InfiniteRepr(),
        BadRepr(),
        {BadKey(): 1},
        b"\xff" * 100,
        bytearray(b"data"),
        (lambda x: x),
        (x for x in range(1)),
        {"deep": {"deep": {"deep": {"deep": {"deep": "x"}}}}},
    ]
    for sample in samples:
        serializer.serialize(sample)
        serializer.serialize_locals({"value": sample})

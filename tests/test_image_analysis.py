from types import SimpleNamespace

from langchain_core.messages import HumanMessage

from image_analysis import analyze_image


class FakeVisionModel:
    def __init__(self, answer="A chart with three bars."):
        self.answer = answer
        self.messages = None

    def invoke(self, messages):
        self.messages = messages
        return SimpleNamespace(content=self.answer)


def test_analyze_image_sends_inline_image_and_user_question(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    image = uploads / "chart.png"
    image.write_bytes(b"fake-png-data")
    model = FakeVisionModel()

    answer = analyze_image(str(image), "What does this chart show?", llm=model)

    assert answer == "A chart with three bars."
    message = model.messages[0]
    assert isinstance(message, HumanMessage)
    assert message.content[0]["type"] == "text"
    assert "What does this chart show?" in message.content[0]["text"]
    assert message.content[1]["type"] == "image_url"
    assert message.content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_analyze_image_rejects_path_outside_uploads(tmp_path):
    image = tmp_path / "private.png"
    image.write_bytes(b"image")
    try:
        analyze_image(str(image), "Describe it", llm=FakeVisionModel())
    except ValueError as exc:
        assert "outside the upload directory" in str(exc)
    else:
        raise AssertionError("Expected path validation to reject external file")

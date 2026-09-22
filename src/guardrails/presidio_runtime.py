"""Construct the optional NLP engine using only the image's frozen assets."""

from presidio_analyzer import AnalyzerEngine
from presidio_analyzer.predefined_recognizers import EmailRecognizer
from tldextract import TLDExtract


class OfflineEmailRecognizer(EmailRecognizer):
    def __init__(self) -> None:
        super().__init__()
        # Empty URLs select the package's bundled PSL snapshot. No HTTP refresh,
        # mutable home-directory cache, or first-request download is permitted.
        self._extract = TLDExtract(cache_dir=None, suffix_list_urls=())

    def validate_result(self, pattern_text: str) -> bool:
        return self._extract(pattern_text).fqdn != ""


def build_analyzer() -> AnalyzerEngine:
    analyzer = AnalyzerEngine()
    analyzer.registry.remove_recognizer("EmailRecognizer")
    analyzer.registry.add_recognizer(OfflineEmailRecognizer())
    return analyzer

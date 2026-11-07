from dataclasses import dataclass

PRODUCT_URL = "https://shop.amul.com/en/product/{alias}"


@dataclass(frozen=True)
class Product:
    alias: str
    name: str
    price: float | None
    available: bool
    quantity: int | None = None

    @property
    def url(self) -> str:
        return PRODUCT_URL.format(alias=self.alias)


class ScrapeError(Exception):
    """The stock check could not produce a trustworthy product list."""

"""
Imagens do projeto da Jornada de Compra nas seções e nos arquivos exportados.

As imagens chegam com categoria, loja, marca, vista e a marca de versão
"editada" (do leitor da pasta ou do upload). Aqui se decide o que mostrar: na
Gôndola, a foto e o heatmap da loja; em Embalagens, as fotos da marca; no PDF
e no PPTX, uma imagem por loja e uma por marca. Sem Streamlit: quem chama
traz os bytes (`jornada_cache.get_image`).
"""

import io
from typing import Dict, Iterable, List, Sequence, Tuple

from utils.jornada_taxonomy import fold

VIEW_ORDER = ("frente", "verso", "lateral", "topo", "fundo")


def _category(image: Dict) -> str:
    return fold(image.get("category") or "")


def _name(image: Dict) -> str:
    return fold(image.get("caption") or image.get("filename") or "")


def store_photos(images: Iterable[Dict], store: str) -> List[Dict]:
    """Fotos de gôndola da loja: a editada primeiro, depois a panorâmica, depois as outras."""
    chosen = [image for image in images if _category(image) == "gondola" and image.get("store") == store]
    return sorted(chosen, key=lambda image: (not image.get("edited"), "panoram" not in _name(image), _name(image)))


def store_heatmaps(images: Iterable[Dict], store: str) -> List[Dict]:
    """Heatmaps da loja (imagem do Blickshift sobre a gôndola)."""
    return sorted((image for image in images if _category(image) == "heatmap" and image.get("store") == store),
                  key=_name)


def brand_photos(images: Iterable[Dict], brand: str) -> List[Dict]:
    """Fotos da embalagem da marca: a editada primeiro, depois frente, verso e laterais."""
    def rank(image: Dict):
        view = image.get("view") or ""
        return (not image.get("edited"), VIEW_ORDER.index(view) if view in VIEW_ORDER else len(VIEW_ORDER),
                _name(image))

    return sorted((image for image in images
                   if _category(image) == "embalagem" and fold(image.get("brand") or "") == fold(brand)), key=rank)


def report_images(images: Sequence[Dict], stores: Sequence[Tuple[str, str]], brands: Sequence[str]) -> List[Dict]:
    """Uma imagem por loja (o heatmap, ou a melhor foto) e uma por marca, para o PDF e o PPTX.

    `stores` traz (chave, rótulo). Cada item volta com `title` e `group`
    ("loja" ou "marca") além dos campos da imagem.
    """
    picked = []
    for store, label in stores:
        options = store_heatmaps(images, store) or store_photos(images, store)
        if options:
            kind = "Heatmap" if _category(options[0]) == "heatmap" else "Gôndola"
            picked.append(dict(options[0], title="{} · {}".format(kind, label), group="loja"))
    for brand in brands:
        options = brand_photos(images, brand)
        if options:
            picked.append(dict(options[0], title="Embalagem · {}".format(brand), group="marca"))
    return picked


def downscale(content: bytes, max_px: int = 1600, quality: int = 82) -> bytes:
    """JPEG com até `max_px` no lado maior (fotos de celular passam de 3 MB); vazio se não for imagem."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(content)) as image:
            image.thumbnail((max_px, max_px))
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, format="JPEG", quality=quality)
    except Exception:
        return b""
    return buffer.getvalue()

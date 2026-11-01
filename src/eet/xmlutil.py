"""Safe XML parsing and canonicalization."""

from __future__ import annotations

from lxml import etree


def parse_xml(data: bytes) -> etree._Element:
    """Parse a message from the network: no entities, no DTD, no network access.

    Raises etree.LxmlError or ValueError when the data is not an acceptable XML document.
    """
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)
    root = etree.fromstring(data, parser)
    if root.getroottree().docinfo.internalDTD is not None:
        raise ValueError("A document type declaration is not allowed.")
    return root


def canonicalize(element: etree._Element, inclusive_prefixes: list[str] | None) -> bytes:
    """Exclusive C14N without comments."""
    return etree.tostring(
        element,
        method="c14n",
        exclusive=True,
        with_comments=False,
        inclusive_ns_prefixes=inclusive_prefixes,
    )


def text_content(element: etree._Element) -> str:
    """Whole text of the element, unlike element.text not cut off by a comment inside."""
    return str(element.xpath("string()")).strip()

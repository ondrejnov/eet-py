# EET 2.0 klient pro Python

Knihovna pro odesílání evidovaných tržeb do systému [EET 2.0](https://eet.gov.cz/cs/pro-vyvojare/dokumenty-ke-stazeni) (rozhraní v4.1, zákon účinný od 1. 1. 2027). Sestaví zprávu, podepíše ji pokladním certifikátem, odešle a ověří podpis potvrzení. Je to port PHP knihovny [`ondrejnov/eet`](https://github.com/ondrejnov/eet).

- Požadavky: Python 3.10 a novější, `cryptography` 45+, `lxml` 5+
- Nepotřebuje WSDL za běhu ani SOAP knihovnu, HTTP obstarává standardní knihovna
- Otestováno proti živému playgroundu na všech třech testovacích identitách

> [!WARNING]
> Tato verze je zatím určená **jen pro playground**. POK z playgroundu (končí `-ff`) není platným potvrzovacím kódem a tržba se jím neeviduje. Produkční prostředí zatím nebylo vyzkoušené.

## Rychlý start

```sh
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python scripts/fetch_playground_certs.py   # stáhne sdílené testovací certifikáty do _cert/
.venv/bin/python examples/simple_client.py
```

```python
from datetime import datetime
from zoneinfo import ZoneInfo

from eet import Certificate, Dispatcher, Receipt

certificate = Certificate.from_pkcs12("_cert/CA_EET-Playground-CZ00000019.p12", heslo)
dispatcher = Dispatcher(Dispatcher.PLAYGROUND_URL, certificate)

receipt = Receipt(
    eic_popl="CZ00000019",
    id_jednotky=181,
    id_pokl="POKLADNA-1",
    porad_cis="2027/0001",
    dat_trzby=datetime.now(ZoneInfo("Europe/Prague")),
    celk_trzba=1000,
)

print(dispatcher.send(receipt))  # POK, např. 80bd7254-14f1-49f8-a332-986a62bcd5a4-ff
print(dispatcher.warnings)       # {kód varování: text}
```

Heslo k testovacím certifikátům je v souboru `_cert/password_pokladni_cert_playground.txt`.

Ověřovací mód (`overeni="true"`) tržbu neeviduje, jen ověří spojení a zpracovatelnost zprávy:

```python
dispatcher.check(receipt)  # True = chyba 0, False = detail v dispatcher.last_response
```

## Položky tržby

Položky lze předat jen pojmenovaně, nebo je nastavit jako atributy.

| Položka | Povinná | Hodnota |
| --- | --- | --- |
| `eic_popl` | ano | EIČ poplatníka, `CZ` + 8 až 10 číslic |
| `id_jednotky` | ano | číslo evidenční jednotky z DIS+, 1 až 999999999 |
| `id_pokl` | ano | označení pokladního zařízení, max. 20 znaků |
| `porad_cis` | ano | pořadové číslo tržby, max. 25 znaků |
| `dat_trzby` | ano | `datetime` s časovou zónou místa tržby |
| `celk_trzba` | ano | částka v Kč: `Decimal`, `int`, `float` nebo číselný řetězec |
| `urceno_cerp_zuct` | ne | platby určené k následnému čerpání nebo zúčtování |
| `cerp_zuct` | ne | platby, které jsou čerpáním nebo zúčtováním |
| `eic_poverujiciho` | ne | EIČ pověřujícího poplatníka |
| `povereni_vice_popl` | ne | tržba plyne více poplatníkům |
| `uuid_zpravy` | ne | prázdné = nové UUID při každém odeslání |
| `prvni_zaslani` | ne | výchozí `True`, nastavuje `for_resend()` |

Před odesláním se hodnoty kontrolují podle masek z XSD. Neplatná tržba se neodešle a skončí výjimkou `ValidationError`. Neznámá nebo zrušená položka (například `dan1`) vyvolá výjimku hned při zápisu, aby z daňové zprávy tiše nezmizela.

> [!IMPORTANT]
> `datetime` bez časové zóny (například `datetime.now()`) se odmítne, protože zónu by bylo nutné hádat. Použijte `datetime.now(ZoneInfo("Europe/Prague"))`.

Částky s více než dvěma desetinnými místy se zaokrouhlí na haléře, půl haléře směrem od nuly. U `float` se bere jeho nejkratší zápis, takže `1.005` je `1.01`. Přesné částky je lepší předávat jako `Decimal`.

## Odpovědi a chyby

Všechny výjimky dědí z `EETError`.

| Výsledek | Význam | Co dělat |
| --- | --- | --- |
| `send()` vrátí POK | tržba přijata | uložit POK, vyhodnotit `dispatcher.warnings` a zalogovat `dispatcher.last_response.transaction_id` |
| `ValidationError` | neplatná data, nic se neodeslalo | opravit data, seznam je v `errors` |
| `TransportError` | timeout, výpadek nebo nečitelná odpověď, stav tržby není známý | co nejdříve poslat `for_resend()` |
| `ServerError`, `is_retryable` je `True` | dočasná chyba serveru (kód -1 nebo 8) | poslat `for_resend()` později |
| `ServerError`, `is_retryable` je `False` | kritická chyba, kód je v `code` | opravit příčinu |
| `ResponseVerificationError` | odpovědi nelze důvěřovat | POK nepoužívat, prověřit |

`Dispatcher` si drží stav posledního volání (`last_response`, `last_request`, `warnings` a další), proto používejte jednu instanci na vlákno.

### Opakované odeslání

Dokud nepřijde POK, je povinnost zaslat tržbu stále aktuální. Lhůta je bezodkladně po pominutí výpadku, nejpozději do 48 hodin. Povinnost trvá i po jejím uplynutí.

```python
dispatcher.send(receipt.for_resend())  # stejná data, nové UUID, prvni_zaslani = False
```

Storno je nová zpráva se zápornou částkou, aktuálním datem a novým UUID. Na původní tržbu se neváže.

## Certifikáty

Certifikát od CA EET se dodává jako `.p12`, knihovna ale načte i PEM:

```python
Certificate.from_pkcs12("pokladna.p12", heslo)
Certificate.from_pem("pokladna.key", "pokladna.pem", heslo)
```

Soubory `.p12` z playgroundu používají zastaralou šifru RC2-40. Oficiální balíčky `cryptography` ji načtou bez další konfigurace, takže na rozdíl od PHP verze není potřeba převod na PEM.

## Zabezpečení

- **Podpis zprávy:** podepisuje se jen `soap:Body` (exclusive C14N, SHA-256, RSA-SHA256). Struktura odpovídá vzorovým zprávám finanční správy a neobsahuje `Timestamp` ani WS-Addressing.
- **Spojení:** jen HTTPS přes HTTP/1.1, TLS 1.2 a vyšší, s ověřením certifikátu serveru. Každý požadavek používá nové spojení, takže se znovu překládá DNS.
- **Potvrzení:** ověřuje se podpis, řetěz k I.CA (`src/eet/resources/ica-signing-ca.pem`), platnost certifikátu, `O = Generální finanční ředitelství` a shoda UUID se zprávou. Podepsané tělo musí být to samé, ze kterého se čte POK, takže útok typu signature wrapping neprojde. CRL ani OCSP se nekontrolují.
- **Chybové odpovědi:** ty finanční správa nepodepisuje, a proto se neověřují.

Výchozí časové limity jsou 2,5 s na celý požadavek včetně překladu DNS a 2 s na navázání spojení. Zákon požaduje mezní dobu odezvy nejméně 2 sekundy. Limity lze změnit vlastním transportem:

```python
Dispatcher(url, certificate, transport=HttpsTransport(timeout=5, connect_timeout=3))
```

## Rozdíly proti PHP verzi

Rozhraní je stejné, jen v konvencích Pythonu: `get_signed_message()`, `last_response`, `ServerError.code` a výjimky s příponou `Error`. Zprávy, tělo požadavku i odpovědi jsou `bytes`.

Chování se liší záměrně v těchto bodech:

- Časové limity se zadávají v sekundách, ne v milisekundách.
- Řetězcová částka musí být v prostém desetinném zápisu, `"1e3"` ani `" 12"` neprojdou.
- Odpověď s neplatným kódem chyby nebo bez potvrzení i chyby je `TransportError`, protože stav tržby není známý. V PHP se takový kód převede na 0, což by ověřovací mód vyhodnotil jako úspěch.
- Klíč jiného typu než RSA se odmítne už při načtení.

## Testy

```sh
.venv/bin/pytest                    # bez sítě
EET_PLAYGROUND=1 .venv/bin/pytest   # včetně živého playgroundu
```

Testovací vektory v `tests/fixtures` tvoří oficiální vzorové zprávy a skutečné odpovědi playgroundu. Certifikáty playgroundu platí do 14. 5. 2027. Playground má servisní okno obvykle ve čtvrtek mezi 20:00 a 6:00.

## In English

Client for the Czech electronic registration of sales, EET 2.0 (interface v4.1), for Python 3.10+. It builds and signs the WS-Security message, sends it over HTTPS and verifies the signed confirmation. This version targets the **non-production playground only**. See the code samples above and `examples/simple_client.py`.

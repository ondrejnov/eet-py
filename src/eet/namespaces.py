"""XML namespaces and algorithm identifiers of the signed EET messages."""

SOAP = "http://schemas.xmlsoap.org/soap/envelope/"
EET = "http://fs.gov.cz/eet/schema/v4"
WSSE = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
WSU = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
DS = "http://www.w3.org/2000/09/xmldsig#"
EXC_C14N = "http://www.w3.org/2001/10/xml-exc-c14n#"

ALGO_RSA_SHA256 = "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"
ALGO_SHA256 = "http://www.w3.org/2001/04/xmlenc#sha256"

TOKEN_ENCODING = (
    "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary"
)
TOKEN_TYPE = (
    "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-x509-token-profile-1.0#X509v3"
)

XPATH_PREFIXES = {
    "s": SOAP,
    "eet": EET,
    "wsse": WSSE,
    "wsu": WSU,
    "ds": DS,
    "ec": EXC_C14N,
}

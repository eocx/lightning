from typing import TextIO
from typing import Tuple
from textwrap import dedent, indent
import logging
import sys
import re

from .model import (ArrayField, CompositeField, EnumField,
                    PrimitiveField, Service)

logger = logging.getLogger(__name__)

# The following words need to be changed, otherwise they'd clash with
# built-in keywords.
keywords = ["in", "type"]

# Manual overrides for some of the auto-generated types for paths
# Manual overrides for some of the auto-generated types for paths
overrides = {
    'ListPeers.peers[].channels[].state_changes[].old_state': "ChannelState",
    'ListPeers.peers[].channels[].state_changes[].new_state': "ChannelState",
    'ListPeers.peers[].channels[].state_changes[].cause': "ChannelStateChangeCause",
    'ListPeers.peers[].channels[].opener': "ChannelSide",
    'ListPeers.peers[].channels[].closer': "ChannelSide",
    'ListPeers.peers[].channels[].features[]': "string",
    'ListFunds.channels[].state': 'ChannelState',
    'ListTransactions.transactions[].type[]': None,
}

# A map of schema type to rust primitive types.
typemap = {
    'boolean': 'bool',
    'hex': 'String',
    'msat': 'Amount',
    'msat|all': 'AmountOrAll',
    'msat|any': 'AmountOrAny',
    'number': 'i64',
    'pubkey': 'String',
    'short_channel_id': 'String',
    'signature': 'String',
    'string': 'String',
    'txid': 'String',
    'float': 'f32',
    'utxo': 'Utxo',
    'feerate': 'Feerate',
}

header = f"""#![allow(non_camel_case_types)]
//! This file was automatically generated using the following command:
//!
//! ```bash
//! {" ".join(sys.argv)}
//! ```
//!
//! Do not edit this file, it'll be overwritten. Rather edit the schema that
//! this file was generated from

"""


def normalize_varname(field):
    """Make sure that the variable name of this field is valid.
    """
    # Dashes are not valid names
    field.path = field.path.replace("-", "_")
    field.path = re.sub(r'(?<!^)(?=[A-Z])', '_', field.path).lower()
    return field


def gen_field(field):
    if isinstance(field, CompositeField):
        return gen_composite(field)
    elif isinstance(field, EnumField):
        return gen_enum(field)
    elif isinstance(field, ArrayField):
        return gen_array(field)
    elif isinstance(field, PrimitiveField):
        return gen_primitive(field)
    else:
        raise ValueError(f"Unmanaged type {field}")


def gen_enum(e):
    defi, decl = "", ""

    if e.description != "":
        decl += f"/// {e.description}\n"

    decl += f"#[derive(Copy, Clone, Debug, Deserialize, Serialize)]\n#[serde(rename_all = \"lowercase\")]\npub enum {e.typename} {{\n"
    for v in e.variants:
        if v is None:
            continue
        norm = v.normalized()
        # decl += f"    #[serde(rename = \"{v}\")]\n"
        decl += f"    {norm},\n"
    decl += "}\n\n"

    # Implement From<i32> so we can convert from the numerical
    # representation
    decl += dedent(f"""\
    impl TryFrom<i32> for {e.typename} {{
        type Error = anyhow::Error;
        fn try_from(c: i32) -> Result<{e.typename}, anyhow::Error> {{
            match c {{
    """)
    for i, v in enumerate(e.variants):
        norm = v.normalized()
        # decl += f"    #[serde(rename = \"{v}\")]\n"
        decl += f"    {i} => Ok({e.typename}::{norm}),\n"
    decl += dedent(f"""\
                o => Err(anyhow::anyhow!("Unknown variant {{}} for enum {e.typename}", o)),
            }}
        }}
    }}
    """)

    typename = e.typename

    if e.path in overrides:
        decl = ""  # No declaration if we have an override
        typename = overrides[e.path]

    if e.required:
        defi = f"    // Path `{e.path}`\n    #[serde(rename = \"{e.name}\")]\n    pub {e.name.normalized()}: {typename},\n"
    else:
        defi = f'    #[serde(skip_serializing_if = "Option::is_none")]\n'
        defi = f"    pub {e.name.normalized()}: Option<{typename}>,\n"

    return defi, decl


def gen_primitive(p):
    defi, decl = "", ""
    org = p.name.name
    typename = typemap.get(p.typename, p.typename)
    normalize_varname(p)

    if p.required:
        defi = f"    #[serde(alias = \"{org}\")]\n    pub {p.name}: {typename},\n"
    else:
        defi = f"    #[serde(alias = \"{org}\", skip_serializing_if = \"Option::is_none\")]\n    pub {p.name}: Option<{typename}>,\n"

    return defi, decl


def gen_array(a):
    name = a.name.normalized().replace("[]", "")
    logger.debug(f"Generating array field {a.name} -> {name} ({a.path})")
    _, decl = gen_field(a.itemtype)

    if a.path in overrides:
        decl = ""  # No declaration if we have an override
        itemtype = overrides[a.path]
    elif isinstance(a.itemtype, PrimitiveField):
        itemtype = a.itemtype.typename
    elif isinstance(a.itemtype, CompositeField):
        itemtype = a.itemtype.typename
    elif isinstance(a.itemtype, EnumField):
        itemtype = a.itemtype.typename

    if itemtype is None:
        return ("", "")  # Override said not to include

    itemtype = typemap.get(itemtype, itemtype)
    alias = a.name.normalized()[:-2]  # Strip the `[]` suffix for arrays.
    defi = f"    #[serde(alias = \"{alias}\")]\n    pub {name}: {'Vec<'*a.dims}{itemtype}{'>'*a.dims},\n"

    return (defi, decl)


def gen_composite(c) -> Tuple[str, str]:
    logger.debug(f"Generating composite field {c.name} ({c.path})")
    fields = []
    for f in c.fields:
        fields.append(gen_field(f))

    r = "".join([f[1] for f in fields])

    r += f"""#[derive(Clone, Debug, Deserialize, Serialize)]\npub struct {c.typename} {{\n"""

    r += "".join([f[0] for f in fields])

    r += "}\n\n"
    return ("", r)


class RustGenerator:
    def __init__(self, dest: TextIO):
        self.dest = dest

    def write(self, text: str, numindent: int = 0) -> None:
        raw = dedent(text)
        if numindent > 0:
            raw = indent(text, "\t" * numindent)
        self.dest.write(raw)

    def generate_requests(self, service: Service):
        self.write("""\
        pub mod requests {
            #[allow(unused_imports)]
            use crate::primitives::*;
            #[allow(unused_imports)]
            use serde::{{Deserialize, Serialize}};

        """)

        for meth in service.methods:
            req = meth.request
            _, decl = gen_composite(req)
            self.write(decl, numindent=1)

        self.write("}\n\n")

    def generate_responses(self, service: Service):
        self.write("""
        pub mod responses {
            #[allow(unused_imports)]
            use crate::primitives::*;
            #[allow(unused_imports)]
            use serde::{{Deserialize, Serialize}};

        """)

        for meth in service.methods:
            res = meth.response
            _, decl = gen_composite(res)
            self.write(decl, numindent=1)

        self.write("}\n\n")

    def generate_enums(self, service: Service):
        """The Request and Response enums serve as parsing primitives.
        """
        self.write(f"""\
        use serde::{{Deserialize, Serialize}};
        pub use requests::*;
        pub use responses::*;

        #[derive(Clone, Debug, Serialize, Deserialize)]
        #[serde(tag = "method", content = "params")]
        #[serde(rename_all = "lowercase")]
        pub enum Request {{
        """)

        for method in service.methods:
            self.write(f"{method.name}(requests::{method.request.typename}),\n", numindent=1)

        self.write(f"""\
        }}

        #[derive(Clone, Debug, Serialize, Deserialize)]
        #[serde(tag = "method", content = "result")]
        #[serde(rename_all = "lowercase")]
        pub enum Response {{
        """)

        for method in service.methods:
            self.write(f"{method.name}(responses::{method.response.typename}),\n", numindent=1)

        self.write(f"""\
        }}

        """)

    def generate(self, service: Service) -> None:
        self.write(header)

        self.generate_enums(service)

        self.generate_requests(service)
        self.generate_responses(service)

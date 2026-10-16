app [main!] {
	cli: platform "https://github.com/roc-lang/basic-cli/releases/download/0.25.0/CZsY7tYZwR3rj9kYbpaCfxki2yVAaRL8bBwMLvB2xkbA.tar.zst",
}

import src/Sha256
main! = |_| Ok({})

# Published FIPS examples and Python hashlib cross-checks at padding boundaries.
expect Sha256.hex("".to_utf8()) == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
expect Sha256.hex("abc".to_utf8()) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
expect Sha256.hex("abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq".to_utf8()) == "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1"
expect Sha256.hex(List.repeat(97, 1)) == "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb"
expect Sha256.hex(List.repeat(97, 55)) == "9f4390f8d30c2dd92ec9f095b65e2b9ae9b0a925a5258e241c9f1e910f734318"
expect Sha256.hex(List.repeat(97, 56)) == "b35439a4ac6f0948b6d6f9e3c6af0f5f590ce20f1bde7090ef7970686ec6738a"
expect Sha256.hex(List.repeat(97, 63)) == "7d3e74a05d7db15bce4ad9ec0658ea98e3f06eeecf16b4c6fff2da457ddc2f34"
expect Sha256.hex(List.repeat(97, 64)) == "ffe054fe7ae0cb6dc65c3af9b61d5209f439851db43d0ba5997337df154668eb"
expect Sha256.hex(List.repeat(97, 65)) == "635361c48bb9eab14198e76ea8ab7f1a41685d6ad62aa9146d301d4f17eb0ae0"
expect Sha256.hex(List.repeat(97, 119)) == "31eba51c313a5c08226adf18d4a359cfdfd8d2e816b13f4af952f7ea6584dcfb"
expect Sha256.hex(List.repeat(97, 120)) == "2f3d335432c70b580af0e8e1b3674a7c020d683aa5f73aaaedfdc55af904c21c"
expect Sha256.hex(List.repeat(97, 127)) == "c57e9278af78fa3cab38667bef4ce29d783787a2f731d4e12200270f0c32320a"
expect Sha256.hex(List.repeat(97, 128)) == "6836cf13bac400e9105071cd6af47084dfacad4e5e302c94bfed24e013afb73e"
expect Sha256.hex(List.repeat(97, 129)) == "c12cb024a2e5551cca0e08fce8f1c5e314555cc3fef6329ee994a3db752166ae"
expect Sha256.hex(List.repeat(97, 1000000)) == "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0"
expect Sha256.hex(all_bytes()) == "40aff2e9d2d8922e47afd4648e6967497158785fbd1da870e7110266bf944880"

all_bytes = || {
	var $bytes = []
	var $n = 0_.U64
	while $n < 256 {
		$bytes = $bytes.append($n.to_u8_wrap())
		$n = $n + 1
	}
	$bytes
}

# Maximum admitted profile size.
expect Sha256.hex(List.repeat(97, 1048576)) == "9bc1b2a288b26af7257a36277ae3816a7d4f16e89c1e7e77d0a5c48bad62b360"

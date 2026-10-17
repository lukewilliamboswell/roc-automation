import deflate.Inflate

## Bounded raw DEFLATE adapter derived from roc-deflate 0.4.0-rc2 Inflate.roc.
## Modified here: output bounds before every append/match, block work quota,
## strict consumed-input check; reuse the pinned package's public decode helpers.
## Licensed Apache-2.0; see vendor/roc-deflate-LICENSE and NOTICE.adoc.
BoundedInflate :: [].{
	decompress : List(U8), U64 -> Try(List(U8), [CorruptData, UnexpectedEnd, OutputLimitExceeded, WorkLimitExceeded, TrailingBytes])
	decompress = |input, limit| {
		if input.len() > 33554432 or limit > 67108864 {
			return Err(OutputLimitExceeded)
		}
		out = List.with_capacity(limit)
		var $blocks = 0.U64
		in_len = List.len(input)

		var $out = out
		var $in_next = 0.U64
		var $bitbuf = 0.U64
		var $bitsleft = 0.U64
		var $overread = 0.U64
		var $more_blocks = True

		# The two big decode tables are built fresh for every block but into
		# the same allocations, which round-trip through build_block_tables
		# and inflate_block back here. Rebuilding writes every entry a decode
		# can read, so the leftover entries never need clearing.
		var $litlen_scratch = List.repeat(0.U32, Inflate.litlen_enough)
		var $offset_scratch = List.repeat(0.U32, Inflate.offset_enough)

		while $more_blocks {
			$blocks = $blocks + 1
			if $blocks > 4096 {
				return Err(WorkLimitExceeded)
			}
			r0 = Inflate.refill(input, $in_next, $bitbuf, $bitsleft, $overread)?
			$in_next = r0.in_next
			$bitbuf = r0.bitbuf
			$bitsleft = r0.bitsleft
			$overread = r0.overread

			$more_blocks = $bitbuf.bitwise_and(1) == 0
			block_type = $bitbuf.shr_zf_wrap(1).bitwise_and(3)

			if block_type == 0 {
				# Stored block: realign to the byte after the header, then
				# copy LEN raw bytes. Any refilled-but-unconsumed whole
				# bytes are returned to the input cursor first.
				$bitsleft = $bitsleft - 3
				unread = $bitsleft.shr_zf_wrap(3)
				if $overread > unread {
					return Err(CorruptData)
				} else {}
				$in_next = $in_next - (unread - $overread)
				$overread = 0
				$bitbuf = 0
				$bitsleft = 0

				if in_len < $in_next + 4 {
					return Err(UnexpectedEnd)
				} else {}
				len_lo = List.get(input, $in_next) ?? 0
				len_hi = List.get(input, $in_next + 1) ?? 0
				nlen_lo = List.get(input, $in_next + 2) ?? 0
				nlen_hi = List.get(input, $in_next + 3) ?? 0
				len = len_lo.to_u64() + len_hi.to_u64().shl_wrap(8)
				nlen = nlen_lo.to_u64() + nlen_hi.to_u64().shl_wrap(8)
				if len + nlen != 0xFFFF {
					return Err(CorruptData)
				} else {}
				$in_next = $in_next + 4
				if in_len < $in_next + len {
					return Err(UnexpectedEnd)
				} else {}
				if len > limit - $out.len() {
					return Err(OutputLimitExceeded)
				}
				$out = List.append_sublist($out, input, { start: $in_next, len })
				$in_next = $in_next + len
			} else if block_type <= 2 {
				tables =
					if block_type == 2 {
						# Dynamic Huffman block: read the header and the
						# run-length-encoded codeword lengths.
						header = Inflate.read_dynamic_header(input, $in_next, $bitbuf, $bitsleft, $overread)?
						$in_next = header.in_next
						$bitbuf = header.bitbuf
						$bitsleft = header.bitsleft
						$overread = header.overread
						Inflate.build_block_tables(header.lens, header.num_litlen_syms, header.num_offset_syms, $litlen_scratch, $offset_scratch)?
					} else {
						# Static Huffman block: the fixed code lengths from
						# RFC 1951 section 3.2.6.
						$bitbuf = $bitbuf.shr_zf_wrap(3)
						$bitsleft = $bitsleft - 3
						lens = List.repeat(8.U8, 144)
							.concat(List.repeat(9.U8, 112))
							.concat(List.repeat(7.U8, 24))
							.concat(List.repeat(8.U8, 8))
							.concat(List.repeat(5.U8, 32))
						Inflate.build_block_tables(lens, 288, 32, $litlen_scratch, $offset_scratch)?
					}

				decoded = inflate_block(input, $in_next, $bitbuf, $bitsleft, $overread, $out, tables, limit)?
				$in_next = decoded.in_next
				$bitbuf = decoded.bitbuf
				$bitsleft = decoded.bitsleft
				$overread = decoded.overread
				$out = decoded.out
				$litlen_scratch = decoded.litlen
				$offset_scratch = decoded.offset
			} else {
				return Err(CorruptData)
			}
		}

		# Consuming any of the implicit appended zero bytes means the stream
		# was truncated.
		if $overread > $bitsleft.shr_zf_wrap(3) {
			Err(UnexpectedEnd)
		} else {
			if $in_next - ($bitsleft.shr_zf_wrap(3) - $overread) != input.len() {
				Err(TrailingBytes)
			} else {
				Ok($out)
			}
		}
	}

	inflate_block : List(U8), U64, U64, U64, U64, List(U8), Inflate.BlockTables, U64 -> Try(Inflate.InflateResult, [CorruptData, UnexpectedEnd, OutputLimitExceeded])
	inflate_block = |input, in_next0, bitbuf0, bitsleft0, overread0, out0, tables, limit| {
		litlen_table = tables.litlen
		litlen_mask = tables.litlen_mask
		offset_table = tables.offset
		# Every root litlen lookup masks with `litlen_mask` and every root
		# offset lookup masks with 255; bounding both tables once keeps
		# those loads in bounds through the whole block.
		if litlen_mask >= List.len(litlen_table) {
			return Err(CorruptData)
		} else {}
		if List.len(offset_table) <= 255 {
			return Err(CorruptData)
		} else {}

		in_len = List.len(input)

		var $in_next = in_next0
		var $bitbuf = bitbuf0
		var $bitsleft = bitsleft0
		var $overread = overread0
		var $out = out0
		var $done = 0.U64

		# ── Fast loop ── decode several symbols per unguarded word refill,
		# decoding ahead by one: every back edge refills and preloads the next
		# symbol's entry, so its table-load latency overlaps the match copy.
		# The 24-byte margin covers any two refills an iteration can perform
		# (each advances the cursor by at most seven bytes and reads eight),
		# counted without path splitting: a cursor within 14 bytes of the
		# iteration start plus an eight-byte read stays inside 24. That bound
		# holds on every path, so the compiler's range proofs discharge each
		# refill's bounds test without reasoning about which paths exclude
		# each other.
		# The entry cursor is caller-controlled; bounding it once lets the
		# compiler's range proofs discharge the margin addition's overflow
		# check on every iteration below.
		if in_next0 > in_len {
			return Err(CorruptData)
		} else {}

		var $entry = 0.U32
		if $in_next + 24 <= in_len {
			word0 = U64.from_le_bytes(input, $in_next) ?? 0
			$bitbuf = $bitbuf.bitwise_or(word0.shl_wrap($bitsleft.to_u8_wrap()))
			$in_next = $in_next + 7 - $bitsleft.shr_zf_wrap(3).bitwise_and(7)
			$bitsleft = $bitsleft.bitwise_or(56)
			$entry = (List.get(litlen_table, $bitbuf.bitwise_and(litlen_mask)) ?? 0)
		} else {}
		while $done == 0 and $in_next + 24 <= in_len {
			var $saved_bitbuf = $bitbuf
			$bitbuf = $bitbuf.shr_zf_wrap($entry.to_u8_wrap())
			$bitsleft = $bitsleft.minus_wrap($entry.to_u64())
			if $entry.bitwise_and(Inflate.huffdec_subtable_pointer) != 0 {
				sub_mask = (1.U64).shl_wrap($entry.shr_zf_wrap(8).bitwise_and(63).to_u8_wrap()) - 1
				sub_index = $entry.shr_zf_wrap(16).to_u64() + $bitbuf.bitwise_and(sub_mask)
				$entry = (List.get(litlen_table, sub_index) ?? 0)
				$saved_bitbuf = $bitbuf
				$bitbuf = $bitbuf.shr_zf_wrap($entry.to_u8_wrap())
				$bitsleft = $bitsleft.minus_wrap($entry.to_u64())
			} else {}

			var $pending = 1.U64
			if $entry.bitwise_and(Inflate.huffdec_literal) != 0 {
				if $out.len() == limit {
					return Err(OutputLimitExceeded)
				}
				$out = List.append($out, $entry.shr_zf_wrap(16).to_u8_wrap())
				$entry = (List.get(litlen_table, $bitbuf.bitwise_and(litlen_mask)) ?? 0)
				$saved_bitbuf = $bitbuf
				$bitbuf = $bitbuf.shr_zf_wrap($entry.to_u8_wrap())
				$bitsleft = $bitsleft.minus_wrap($entry.to_u64())
				if $entry.bitwise_and(Inflate.huffdec_subtable_pointer) != 0 {
					sm2 = (1.U64).shl_wrap($entry.shr_zf_wrap(8).bitwise_and(63).to_u8_wrap()) - 1
					si2 = $entry.shr_zf_wrap(16).to_u64() + $bitbuf.bitwise_and(sm2)
					$entry = (List.get(litlen_table, si2) ?? 0)
					$saved_bitbuf = $bitbuf
					$bitbuf = $bitbuf.shr_zf_wrap($entry.to_u8_wrap())
					$bitsleft = $bitsleft.minus_wrap($entry.to_u64())
				} else {}
				if $entry.bitwise_and(Inflate.huffdec_literal) != 0 {
					if $out.len() == limit {
						return Err(OutputLimitExceeded)
					}
					$out = List.append($out, $entry.shr_zf_wrap(16).to_u8_wrap())
					$entry = (List.get(litlen_table, $bitbuf.bitwise_and(litlen_mask)) ?? 0)
					$saved_bitbuf = $bitbuf
					$bitbuf = $bitbuf.shr_zf_wrap($entry.to_u8_wrap())
					$bitsleft = $bitsleft.minus_wrap($entry.to_u64())
					if $entry.bitwise_and(Inflate.huffdec_subtable_pointer) != 0 {
						sm3 = (1.U64).shl_wrap($entry.shr_zf_wrap(8).bitwise_and(63).to_u8_wrap()) - 1
						si3 = $entry.shr_zf_wrap(16).to_u64() + $bitbuf.bitwise_and(sm3)
						$entry = (List.get(litlen_table, si3) ?? 0)
						$saved_bitbuf = $bitbuf
						$bitbuf = $bitbuf.shr_zf_wrap($entry.to_u8_wrap())
						$bitsleft = $bitsleft.minus_wrap($entry.to_u64())
					} else {}
					if $entry.bitwise_and(Inflate.huffdec_literal) != 0 {
						if $out.len() == limit {
							return Err(OutputLimitExceeded)
						}
						$out = List.append($out, $entry.shr_zf_wrap(16).to_u8_wrap())
						word3 = U64.from_le_bytes(input, $in_next) ?? 0
						$bitbuf = $bitbuf.bitwise_or(word3.shl_wrap($bitsleft.to_u8_wrap()))
						$in_next = $in_next + 7 - $bitsleft.shr_zf_wrap(3).bitwise_and(7)
						$bitsleft = $bitsleft.bitwise_or(56)
						$entry = (List.get(litlen_table, $bitbuf.bitwise_and(litlen_mask)) ?? 0)
						$pending = 0
					} else {}
				} else {}
			} else {}

			if $pending == 1 {
				if $entry.bitwise_and(Inflate.huffdec_end_of_block) != 0 {
					$done = 1
				} else {
					len_codeword_bits = $entry.shr_zf_wrap(8).bitwise_and(255).to_u8_wrap()
					len_mask = (1.U64).shl_wrap($entry.to_u8_wrap()) - 1
					length = $entry.shr_zf_wrap(16).to_u64()
						+ $saved_bitbuf.bitwise_and(len_mask).shr_zf_wrap(len_codeword_bits)

					if $bitsleft.bitwise_and(255) < 28 {
						word_r = U64.from_le_bytes(input, $in_next) ?? 0
						$bitbuf = $bitbuf.bitwise_or(word_r.shl_wrap($bitsleft.to_u8_wrap()))
						$in_next = $in_next + 7 - $bitsleft.shr_zf_wrap(3).bitwise_and(7)
						$bitsleft = $bitsleft.bitwise_or(56)
					} else {}

					var $off_entry = (List.get(offset_table, $bitbuf.bitwise_and(255)) ?? 0)
					if $off_entry.bitwise_and(Inflate.huffdec_exceptional) != 0 {
						$bitbuf = $bitbuf.shr_zf_wrap(8)
						$bitsleft = $bitsleft.minus_wrap(8)
						osm = (1.U64).shl_wrap($off_entry.shr_zf_wrap(8).bitwise_and(63).to_u8_wrap()) - 1
						osi = $off_entry.shr_zf_wrap(16).to_u64() + $bitbuf.bitwise_and(osm)
						$off_entry = (List.get(offset_table, osi) ?? 0)
					} else {}
					off_codeword_bits = $off_entry.shr_zf_wrap(8).bitwise_and(255).to_u8_wrap()
					off_mask = (1.U64).shl_wrap($off_entry.to_u8_wrap()) - 1
					offset = $off_entry.shr_zf_wrap(16).to_u64()
						+ $bitbuf.bitwise_and(off_mask).shr_zf_wrap(off_codeword_bits)
					$bitbuf = $bitbuf.shr_zf_wrap($off_entry.to_u8_wrap())
					$bitsleft = $bitsleft.minus_wrap($off_entry.to_u64())

					out_len = List.len($out)
					if offset > out_len or offset == 0 {
						return Err(CorruptData)
					} else {}

					# Refill and preload the next symbol before the copy runs,
					# so its table-load latency hides under the copy's stores.
					word2 = U64.from_le_bytes(input, $in_next) ?? 0
					$bitbuf = $bitbuf.bitwise_or(word2.shl_wrap($bitsleft.to_u8_wrap()))
					$in_next = $in_next + 7 - $bitsleft.shr_zf_wrap(3).bitwise_and(7)
					$bitsleft = $bitsleft.bitwise_or(56)
					$entry = (List.get(litlen_table, $bitbuf.bitwise_and(litlen_mask)) ?? 0)

					if length > limit - out_len {
						return Err(OutputLimitExceeded)
					}
					$out = match List.append_range_within($out, out_len - offset, length) {
						Ok(new_out) => new_out
						Err(_) => return Err(CorruptData)
					}
				}
			} else {}
		}

		# ── Careful loop ── one symbol per iteration, tail of input.
		while $done == 0 {
			if $bitsleft.bitwise_and(255) < 32 {
				r = Inflate.refill(input, $in_next, $bitbuf, $bitsleft, $overread)?
				$in_next = r.in_next
				$bitbuf = r.bitbuf
				$bitsleft = r.bitsleft
				$overread = r.overread
			} else {}

			$entry = List.get(litlen_table, $bitbuf.bitwise_and(litlen_mask)) ?? 0
			var $saved_bitbuf = $bitbuf
			$bitbuf = $bitbuf.shr_zf_wrap($entry.to_u8_wrap())
			$bitsleft = $bitsleft.minus_wrap($entry.to_u64())

			if $entry.bitwise_and(Inflate.huffdec_subtable_pointer) != 0 {
				sub_mask = (1.U64).shl_wrap($entry.shr_zf_wrap(8).bitwise_and(63).to_u8_wrap()) - 1
				sub_index = $entry.shr_zf_wrap(16).to_u64() + $bitbuf.bitwise_and(sub_mask)
				$entry = List.get(litlen_table, sub_index) ?? 0
				$saved_bitbuf = $bitbuf
				$bitbuf = $bitbuf.shr_zf_wrap($entry.to_u8_wrap())
				$bitsleft = $bitsleft.minus_wrap($entry.to_u64())
			} else {}

			if $entry.bitwise_and(Inflate.huffdec_literal) != 0 {
				if $out.len() == limit {
					return Err(OutputLimitExceeded)
				}
				$out = List.append($out, $entry.shr_zf_wrap(16).to_u8_wrap())
			} else if $entry.bitwise_and(Inflate.huffdec_end_of_block) != 0 {
				break
			} else {
				# Length: base plus extra bits, both packed in the entry.
				len_codeword_bits = $entry.shr_zf_wrap(8).bitwise_and(255).to_u8_wrap()
				len_mask = (1.U64).shl_wrap($entry.to_u8_wrap()) - 1
				length = $entry.shr_zf_wrap(16).to_u64()
					+ $saved_bitbuf.bitwise_and(len_mask).shr_zf_wrap(len_codeword_bits)

				# Offset: up to 15+13 codeword and extra bits, plus the 32
				# already guaranteed, always fit after one refill.
				if $bitsleft.bitwise_and(255) < 28 {
					r2 = Inflate.refill(input, $in_next, $bitbuf, $bitsleft, $overread)?
					$in_next = r2.in_next
					$bitbuf = r2.bitbuf
					$bitsleft = r2.bitsleft
					$overread = r2.overread
				} else {}

				var $off_entry = List.get(offset_table, $bitbuf.bitwise_and(255)) ?? 0
				if $off_entry.bitwise_and(Inflate.huffdec_exceptional) != 0 {
					$bitbuf = $bitbuf.shr_zf_wrap(8)
					$bitsleft = $bitsleft.minus_wrap(8)
					off_sub_mask = (1.U64).shl_wrap($off_entry.shr_zf_wrap(8).bitwise_and(63).to_u8_wrap()) - 1
					off_sub_index = $off_entry.shr_zf_wrap(16).to_u64() + $bitbuf.bitwise_and(off_sub_mask)
					$off_entry = List.get(offset_table, off_sub_index) ?? 0
				} else {}
				off_codeword_bits = $off_entry.shr_zf_wrap(8).bitwise_and(255).to_u8_wrap()
				off_mask = (1.U64).shl_wrap($off_entry.to_u8_wrap()) - 1
				offset = $off_entry.shr_zf_wrap(16).to_u64()
					+ $bitbuf.bitwise_and(off_mask).shr_zf_wrap(off_codeword_bits)
				$bitbuf = $bitbuf.shr_zf_wrap($off_entry.to_u8_wrap())
				$bitsleft = $bitsleft.minus_wrap($off_entry.to_u64())

				out_len = List.len($out)
				if offset > out_len or offset == 0 {
					return Err(CorruptData)
				} else {}

				# The match is `length` bytes starting `offset` back in the
				# output; reading through freshly appended bytes is what
				# makes an overlapping range repeat, exactly the copy
				# `append_range_within` performs.
				if length > limit - out_len {
					return Err(OutputLimitExceeded)
				}
				$out = match List.append_range_within($out, out_len - offset, length) {
					Ok(new_out) => new_out
					Err(_) => return Err(CorruptData)
				}
			}
		}

		Ok({
			in_next: $in_next,
			bitbuf: $bitbuf,
			bitsleft: $bitsleft.bitwise_and(255),
			overread: $overread,
			out: $out,
			litlen: litlen_table,
			offset: offset_table,
		})
	}
}

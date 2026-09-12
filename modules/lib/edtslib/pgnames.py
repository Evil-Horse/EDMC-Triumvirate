from . import pgdata

import sys

# #
# util
# #

# 32-bit hashing algorithm found at http://papa.bretmulvey.com/post/124027987928/hash-functions
# Seemingly originally by Bob Jenkins <bob_jenkins-at-burtleburtle.net> in the 1990s
def jenkins32(key):
  key += (key << 12)
  key &= 0xFFFFFFFF
  key ^= (key >> 22)
  key += (key << 4)
  key &= 0xFFFFFFFF
  key ^= (key >> 9)
  key += (key << 10)
  key &= 0xFFFFFFFF
  key ^= (key >> 2)
  key += (key << 7)
  key &= 0xFFFFFFFF
  key ^= (key >> 12)
  return key

def _is_str_py3(s): return isinstance(s, str)
def _is_str_py2(s): return isinstance(s, basestring)
is_str = _is_str_py3 if sys.version_info >= (3, 0) else _is_str_py2

# Deinterleaves two values, starting at least significant bit
# e.g. (0b00110010) --> (0b0100, 0b0101)
def deinterleave(val, maxbits):
  out1 = 0
  out2 = 0
  for i in range(0, maxbits, 2):
    out1 |= ((val >> i) & 1) << (i//2)
  for i in range(1, maxbits, 2):
    out2 |= ((val >> i) & 1) << (i//2)
  return (out1, out2)

# #
# Publicly-useful functions
# #

def get_sector_name(offset):
  """
  Get the name of a sector that a position falls within.

  Args:
    pos: A position
    format_output: Whether or not to format the output or return it as fragments
  Returns:
    The name of the sector which contains the input position, either as a string or as a list of fragments
  """
  if _get_c1_or_c2(offset) == 1:
    output = _c1_get_name_from_offset(offset)
  else:
    output = _c2_get_name_from_offset(offset)

  return format_sector_name(output)


def get_sector_fragments(sector_name, allow_long = False):
  """
  Get a list of fragments from an input sector name
  e.g. "Dryau Aowsy" --> ["Dry","au","Ao","wsy"]

  Args:
    sector_name: The name of the sector
    allow_long: Whether to allow sector names longer than the usual maximum fragment count (4)
  Returns:
    A list of fragments representing the sector name
  """
  # Convert the string to Title Case, then remove spaces
  sector_name = sector_name.title().replace(' ', '')
  segments = []
  current_str = sector_name
  while len(current_str) > 0:
    found = False
    for frag in pgdata.cx_fragments:
      if current_str[0:len(frag)] == frag:
        segments.append(frag)
        current_str = current_str[len(frag):]
        found = True
        break
    if not found:
      break
  if len(current_str) == 0 and (allow_long or len(segments) <= _expected_fragment_limit):
    return segments
  else:
    return None


def format_sector_name(input):
  """
  Format a given set of fragments into a full name

  Args:
    input: A list of sector name fragments
  Returns:
    The sector name as a string
  """
  frags = get_sector_fragments(input) if is_str(input) else input
  if frags is None:
    return None
  if len(frags) == 4 and frags[2] in pgdata.cx_prefixes:
    return "{0}{1} {2}{3}".format(*frags)
  else:
    return "".join(frags)


# #
# Internal variables
# #

_expected_fragment_limit = 4


# Get the full list of suffixes for a given set of fragments missing a suffix
# e.g. "Dryau Ao", "Ogair", "Wreg"
def _get_suffixes(input, get_all = False):
  frags = get_sector_fragments(input) if is_str(input) else input
  if frags is None:
    return None
  wordstart = frags[0]
  if frags[-1] in pgdata.cx_prefixes:
    # Append suffix straight onto a prefix (probably C2)
    suffix_map_idx = pgdata.c2_prefix_suffix_override_map.get(frags[-1], 1)
    result = pgdata.c2_suffixes[suffix_map_idx]
    wordstart = frags[-1]
  else:
    # Likely C1
    if frags[-1] in pgdata.c1_infixes[2]:
      # Last infix is consonant-ish, return the vowel-ish suffix list
      result = pgdata.c1_suffixes[1]
    else:
      result = pgdata.c1_suffixes[2]

  if get_all:
    return result
  else:
    return result[0 : _get_prefix_run_length(wordstart)]


# Get the specified prefix's run length (e.g. Th => 35, Tz => 1)
def _get_prefix_run_length(frag):
  return pgdata.cx_prefix_length_overrides.get(frag, pgdata.cx_prefix_length_default)


def _get_entry_from_offset(offset, keys, data):
  return [c for c in keys if offset >= data[c][0] and offset < (data[c][0] + data[c][1])][0]


# Determines whether a given sector should be C1 or C2
def _get_c1_or_c2(key):
  # Use Jenkins hash
  key = jenkins32(key)
  # Key is now an even/odd number, depending on which scheme we use
  # Return 1 for a class 1 sector, 2 for a class 2
  return (key % 2) + 1


# #
# Internal functions: c1-specific
# #

# Get the full list of infixes for a given set of fragments missing an infix
# e.g. "Ogai", "Wre", "P"
def _c1_get_infixes(input):
  frags = get_sector_fragments(input) if is_str(input) else input
  if frags is None:
    return None
  if frags[-1] in pgdata.cx_prefixes:
    if frags[-1] in pgdata.c1_prefix_infix_override_map:
      return pgdata.c1_infixes[pgdata.c1_prefix_infix_override_map[frags[-1]]]
    else:
      return pgdata.c1_infixes[1]
  elif frags[-1] in pgdata.c1_infixes[1]:
    return pgdata.c1_infixes[2]
  elif frags[-1] in pgdata.c1_infixes[2]:
    return pgdata.c1_infixes[1]
  else:
    return None


# Get the specified infix's run length
def _c1_get_infix_run_length(frag):
  if frag in pgdata.c1_infixes_s1:
    def_len = pgdata.c1_infix_s1_length_default
  else:
    def_len = pgdata.c1_infix_s2_length_default
  return pgdata.c1_infix_length_overrides.get(frag, def_len)


# Get the total run length for the series of infixes the input is part of
def _c1_get_infix_total_run_length(frag):
  if frag in pgdata.c1_infixes_s1:
    return pgdata.c1_infix_s1_total_run_length
  else:
    return pgdata.c1_infix_s2_total_run_length


def _c1_get_name_from_offset(offset):
  # Get the current prefix run we're on, and keep the remaining offset
  prefix_cnt, cur_offset = divmod(offset, pgdata.cx_prefix_total_run_length)
  # Work out which prefix we're currently within
  prefix = _get_entry_from_offset(cur_offset, _prefix_offsets, _prefix_offsets)
  # Put us in that prefix's space
  cur_offset -= _prefix_offsets[prefix][0]

  # Work out which set of infix1s we should be using, and its total length
  infix1s = _c1_get_infixes([prefix])
  infix1_total_len = _c1_get_infix_total_run_length(infix1s[0])
  # Work out where we are in infix1 space, keep the remaining offset
  infix1_cnt, cur_offset = divmod(prefix_cnt * _get_prefix_run_length(prefix) + cur_offset, infix1_total_len)
  # Find which infix1 we're currently in
  infix1 = _get_entry_from_offset(cur_offset, infix1s, _c1_infix_offsets)
  # Put us in that infix1's space
  cur_offset -= _c1_infix_offsets[infix1][0]

  # Work out which set of suffixes we're using
  infix1_run_len = _c1_get_infix_run_length(infix1)
  sufs = _get_suffixes([prefix, infix1], True)
  # Get the index of the next entry in that list, in infix1 space
  next_idx = (infix1_run_len * infix1_cnt) + cur_offset

  # Start creating our output
  frags = [prefix, infix1]

  # If the index of the next entry is longer than the list of suffixes...
  # This means we've gone over all the 3-phoneme names and started the 4-phoneme ones
  # So, we need to calculate our extra phoneme (infix2) before adding a suffix
  if next_idx >= len(sufs):
    # Work out which set of infix2s we should be using
    infix2s = _c1_get_infixes(frags)
    infix2_total_len = _c1_get_infix_total_run_length(infix2s[0])
    # Work out where we are in infix2 space, still keep the remaining offset
    infix2_cnt, cur_offset = divmod(infix1_cnt * _c1_get_infix_run_length(infix1) + cur_offset, infix2_total_len)
    # Find which infix2 we're currently in
    infix2 = _get_entry_from_offset(cur_offset, infix2s, _c1_infix_offsets)
    # Put us in this infix2's space
    cur_offset -= _c1_infix_offsets[infix2][0]

    # Recalculate the next system index based on the infix2 data
    infix2_run_len = _c1_get_infix_run_length(infix2)
    sufs = _get_suffixes([prefix, infix1, infix2], True)
    next_idx = (infix2_run_len * infix2_cnt) + cur_offset

    # Add our infix2 to the output
    frags.append(infix2)

  # Add our suffix to the output, and return it
  frags.append(sufs[next_idx])
  return frags


# #
# Internal functions: c2-specific
# #


def _c2_get_name_from_offset(offset, format_output=False):
  # Deinterleave the two offsets from the single big one
  cur_idx0, cur_idx1 = deinterleave(offset, 32)  # No idea what length this actually is

  # Get prefixes/suffixes from the individual offsets
  p0 = _get_entry_from_offset(cur_idx0, _prefix_offsets, _prefix_offsets)
  p1 = _get_entry_from_offset(cur_idx1, _prefix_offsets, _prefix_offsets)
  s0 = _get_suffixes(p0)[cur_idx0 - _prefix_offsets[p0][0]]
  s1 = _get_suffixes(p1)[cur_idx1 - _prefix_offsets[p1][0]]

  # Done!
  output = [p0, s0, p1, s1]
  if format_output:
    output = format_sector_name(output)
  return output


# #
# Setup functions
# #

# Cache the run offsets of all prefixes and C1 infixes
_prefix_offsets = {}
_c1_infix_offsets = {}
def _construct_offsets():
  global _prefix_offsets, _c1_infix_offsets
  cnt = 0
  for p in pgdata.cx_prefixes:
    plen = _get_prefix_run_length(p)
    _prefix_offsets[p] = (cnt, plen)
    cnt += plen
  cnt = 0
  for i in pgdata.c1_infixes_s1:
    ilen = _c1_get_infix_run_length(i)
    _c1_infix_offsets[i] = (cnt, ilen)
    cnt += ilen
  cnt = 0
  for i in pgdata.c1_infixes_s2:
    ilen = _c1_get_infix_run_length(i)
    _c1_infix_offsets[i] = (cnt, ilen)
    cnt += ilen


# #
# Initialisation
# #

_construct_offsets()

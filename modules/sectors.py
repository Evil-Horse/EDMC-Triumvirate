from modules.edtslib import pgnames

def split_ids(id64):
    # filter out body id in case of any given
    body_id = id64 >> 55
    id64 &= 0x7fffffffffffff

    masscode = id64 & 0x07 # [a-h] -> 0-7
    id64 >>= 3

    boxel_z = id64 & (0x7f >> masscode)
    id64 >>= 7 - masscode

    sector_z = id64 & 0x7f
    id64 >>= 7

    boxel_y = id64 & (0x7f >> masscode)
    id64 >>= 7 - masscode

    sector_y = id64 & 0x3f
    id64 >>= 6

    boxel_x = id64 & (0x7f >> masscode)
    id64 >>= 7 - masscode

    sector_x = id64 & 0x7f
    id64 >>= 7

    sector_offset = (sector_z << 14) + (sector_y << 7) + sector_x
    boxel_offset = (boxel_z << 14) + (boxel_y << 7) + boxel_x

    system_id = id64
    return sector_offset, masscode, boxel_offset, system_id, body_id

def get_sector(sector_offset):
    return pgnames.get_sector_name(sector_offset)

def get_masscode(mcode):
    return chr(mcode + ord('a'))

def get_children(masscode_id, parent_boxel_id):
    if masscode_id == 0: # masscode a, does not have any children
        return []

    parent_boxel_x = parent_boxel_id & 0x7f
    parent_boxel_id >>= 7
    parent_boxel_y = parent_boxel_id & 0x7f
    parent_boxel_id >>= 7
    parent_boxel_z = parent_boxel_id & 0x7f

    children = []
    for i in range(8):
        child_boxel_x = (parent_boxel_x << 1) | ((i & 0x1) >> 0)
        child_boxel_y = (parent_boxel_y << 1) | ((i & 0x2) >> 1)
        child_boxel_z = (parent_boxel_z << 1) | ((i & 0x4) >> 2)

        child_boxel = (child_boxel_z << 14) + (child_boxel_y << 7) + child_boxel_x
        children.append((masscode_id - 1, child_boxel))

    return children

def get_boxel(masscode, boxel_offset):
    # generate [L1][L2]-[L3] [MCode][N1]-N2
    l1 = chr(boxel_offset % 26 + ord('A'))
    boxel_offset //= 26

    l2 = chr(boxel_offset % 26 + ord('A'))
    boxel_offset //= 26

    l3 = chr(boxel_offset % 26 + ord('A'))
    boxel_offset //= 26

    n1 = boxel_offset
    mcode = get_masscode(masscode)

    return f'{l1}{l2}-{l3} {mcode}{n1}'

def get_procgen_name(sector_offset, masscode, boxel_offset, system_id):
    sector = get_sector(sector_offset)

    # generate [L1][L2]-[L3] [MCode][N1]-[N2]
    l1 = chr(boxel_offset % 26 + ord('A'))
    boxel_offset //= 26

    l2 = chr(boxel_offset % 26 + ord('A'))
    boxel_offset //= 26

    l3 = chr(boxel_offset % 26 + ord('A'))
    boxel_offset //= 26

    n1 = boxel_offset
    mcode = get_masscode(masscode)

    if n1 == 0:
        return f'{sector} {l1}{l2}-{l3} {mcode}{system_id}'
    else:
        return f'{sector} {l1}{l2}-{l3} {mcode}{n1}-{system_id}'

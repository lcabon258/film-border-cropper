"""Find two 35 mm half frames on a positive scan, with independent abstention."""
from __future__ import annotations

import cv2
import numpy as np

from film_border_core import grayscale_float, robust_line, _intersect

CORE_VERSION = 'half-frame-v1'
LAYOUTS = {'full': ('',), 'half-lr': ('A', 'B'), 'half-tb': ('A', 'B')}


def _edge(gray, side, bounds, along_bounds, guide=None):
    """Vote for a line using dark-to-light transitions across the frame."""
    vertical = side in ('left', 'right')
    scan_image = gray if vertical else gray.T
    reverse = side in ('right', 'bottom')
    limit = scan_image.shape[1]
    start, end = bounds
    if reverse:
        scan_image = scan_image[:, ::-1]
        start, end = limit - 1 - end, limit - 1 - start
    start, end = max(10, start), min(limit - 12, end)
    if end <= start:
        raise ValueError('搜尋範圍不足')

    along = np.linspace(*along_bounds, 120).round().astype(int)
    scans = cv2.GaussianBlur(np.ascontiguousarray(scan_image[along]), (9, 1), 1.6)
    response = np.zeros_like(scans)
    response[:, 10:-10] = (scans[:, 13:-7] - scans[:, 7:-13])
    rows = np.arange(along.size)[:, None]
    center = (along_bounds[0] + along_bounds[1]) / 2
    positions = np.arange(start, end + 1, 2)
    darkness_limit = min(120., max(66., float(np.percentile(scans[:, start-7:end+1], 20)) + 50.))
    expected = None
    if guide:
        expected = guide['slope'] * along + guide['intercept']
        if reverse:
            expected = limit - 1 - expected

    def in_search_band(depth):
        valid = (depth >= start) & (depth <= end)
        if expected is not None:
            valid &= np.abs(depth - expected[:, None]) <= .025 * limit
        return valid

    def evidence(depth):
        outside = (scans[rows, depth - 5] + scans[rows, depth - 9]) / 2
        inside = (scans[rows, depth + 5] + scans[rows, depth + 9]) / 2
        farther = scans[rows, depth - 10]
        gradient = response[rows, depth]
        contrast = inside - np.maximum(outside, farther - 4)
        dark = np.clip((darkness_limit - outside) / 32, 0, 1)
        vote = np.clip((gradient - 2) / 13, 0, 1) * np.clip((contrast - 4) / 19, 0, 1) * dark
        vote *= .72 + .28 * np.clip(gradient / 110, 0, 1)
        return vote, gradient, contrast, outside

    best = (-1., 0., start)
    for slope in np.linspace(-.055, .055, 45):
        depth = (positions[None, :] + slope * (along[:, None] - center)).round().astype(int)
        valid = in_search_band(depth)
        vote, _, _, _ = evidence(np.clip(depth, start, end))
        scores = (vote * valid).mean(axis=0)
        i = int(np.argmax(scores))
        if scores[i] > best[0]:
            best = float(scores[i]), float(slope), int(positions[i])

    score, slope, position = best
    depth = (position + slope * (along - center)).round().astype(int)
    local = np.clip(depth[:, None] + np.arange(-4, 5), start, end)
    vote, gradient, contrast, outside = evidence(local)
    inside_band = in_search_band(local)
    peak = np.argmax(vote * inside_band, axis=1)
    i = np.arange(along.size)
    valid = (inside_band[i, peak] & (gradient[i, peak] >= 4)
             & (contrast[i, peak] >= 6) & (outside[i, peak] < darkness_limit))
    if score < .13 or valid.mean() < .34:
        raise ValueError('明暗轉折不足')
    observed = local[i, peak]
    slope, intercept, rms, _ = robust_line(along[valid], observed[valid], np.minimum(gradient[i, peak][valid], 28))
    agreement = valid & (np.abs(observed - (slope * along + intercept)) <= 3.5)
    support = float(agreement.mean())
    spread = sum(1 for group in np.array_split(agreement, 5) if group.mean() >= .23)
    if support < .33 or spread < 3 or rms > 4:
        raise ValueError('邊界不夠連續')
    if reverse:
        slope, intercept = -slope, limit - 1 - intercept
    return {'slope': slope, 'intercept': intercept, 'rms_px': rms,
            'support': support, 'spread': spread, 'candidate_score': score}


def detect_half_frames(image, layout):
    if layout not in ('half-lr', 'half-tb'):
        raise ValueError('請指定半格排列。')
    # Transpose for top/bottom scans. The original image is neither rotated nor resampled for export.
    scan = image if layout == 'half-lr' else image.swapaxes(0, 1)
    source_h, source_w = scan.shape[:2]
    scale = min(1., 1280 / max(source_h, source_w))
    w, h = round(source_w * scale), round(source_h * scale)
    if w < 180 or h < 150:
        return [(None, '照片太小，請手動框選。')] * 2
    sample = cv2.resize(scan, (w, h), interpolation=cv2.INTER_AREA) if scale < 1 else scan
    gray = grayscale_float(sample)
    vertical_scan = (round(.12*h), round(.88*h))
    # Each inside edge has the opposite gradient sign. Search both independently,
    # including room for asymmetrically positioned film mounts and scans.
    bounds = [
        ((.004, .12), (.34, .55), (.10, .40)),
        ((.45, .66), (.88, .996), (.60, .90)),
    ]
    searches = []
    frame_edges = []
    errors = []
    for left_span, right_span, along_span in bounds:
        horizontal_scan = tuple(round(x*w) for x in along_span)
        spans = {side: (tuple(round(x*size) for x in span), along)
                 for side, span, size, along in [
                     ('left', left_span, w, vertical_scan),
                     ('right', right_span, w, vertical_scan),
                     ('top', (.014, .115), h, horizontal_scan),
                     ('bottom', (.885, .986), h, horizontal_scan)]}
        searches.append(spans)
        frame_edges.append({})
        errors.append({})
        for side, (span, along) in spans.items():
            try:
                frame_edges[-1][side] = _edge(gray, side, span, along)
            except ValueError as error:
                errors[-1][side] = str(error)

    # If one top/bottom line is far from the continuation of its neighbor,
    # search there again, still requiring actual edge evidence in that frame.
    middle = (w - 1) / 2
    for side in ('top', 'bottom'):
        first, second = (edges.get(side) for edges in frame_edges)
        if first and second:
            separation = abs((first['slope'] - second['slope']) * middle
                             + first['intercept'] - second['intercept'])
            if separation <= .027*h:
                continue
            weak = int(first['candidate_score'] >= second['candidate_score'])
        elif first or second:
            weak = 0 if first is None else 1
        else:
            continue
        guide = frame_edges[1-weak][side]
        if guide['support'] < .65 or not {'left', 'right'} <= frame_edges[weak].keys():
            continue
        try:
            frame_edges[weak][side] = _edge(gray, side, *searches[weak][side], guide=guide)
            errors[weak].pop(side, None)
        except ValueError:
            frame_edges[weak].pop(side, None)
            errors[weak][side] = '與另一格不一致且缺乏足夠證據'

    results = []
    candidates = []
    side_names = dict(left='左', right='右', top='上', bottom='下')
    if layout == 'half-tb':
        side_names = dict(left='上', right='下', top='左', bottom='右')
    for edges, missing in zip(frame_edges, errors):
        try:
            if missing:
                raise ValueError('、'.join(f'{side_names[side]}邊{reason}' for side, reason in missing.items()))
            l, r, t, b = (edges[key] for key in ('left', 'right', 'top', 'bottom'))
            quad = np.array([_intersect(l, t), _intersect(r, t), _intersect(r, b), _intersect(l, b)])
            widths = quad[[1, 2], 0] - quad[[0, 3], 0]
            heights = quad[[3, 2], 1] - quad[[0, 1], 1]
            if (min(widths) < .30*w or max(widths) > .55*w or min(heights) < .74*h
                    or not .49 < widths.mean()/heights.mean() < .92
                    or (quad < 0).any() or (quad >= [w, h]).any()):
                raise ValueError('四邊不符合半格幾何')
            candidates.append(quad)
            original = (quad + .5) * [source_w/w, source_h/h] - .5
            if layout == 'half-tb':
                original = original[[0, 3, 2, 1], ::-1]
            score = min(edge['support'] for edge in edges.values())
            results.append(({
                'corners': original.tolist(),
                'edges': edges,
                'confidence_heuristic': round(score, 3),
                'confidence_level_provisional': 'medium' if score >= .58 else 'low',
                'confidence_calibrated': False,
                'automatic_crop_allowed': False,
                'core_version': CORE_VERSION,
            }, None))
        except ValueError as error:
            candidates.append(None)
            results.append((None, f'{error}，請手動框選。'))
    if all(quad is not None for quad in candidates):
        a, b = candidates
        gap = b[[0, 3], 0] - a[[1, 2], 0]
        if min(gap) < .009*w or max(gap) > .18*w:
            return [(None, '兩格之間沒有可靠的分隔帶，請手動框選。')] * 2
    return results

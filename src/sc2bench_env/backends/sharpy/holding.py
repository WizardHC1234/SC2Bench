"""Compact army stations near the entrances of an owned plateau."""
from collections import deque
from math import floor
from sc2.position import Point2
from types import SimpleNamespace


def overlaps_townhall_site(position, center, radius=0):
    """A ground unit's disk intersects the common 5x5 townhall footprint."""
    dx = max(abs(position.x-center.x)-2.5, 0)
    dy = max(abs(position.y-center.y)-2.5, 0)
    return dx*dx+dy*dy <= (radius+.25)**2


def expansion_station(ai, center):
    """Hold near the zone center while leaving its future townhall site clear.

    The station depends on terrain and buildings, never temporary troop occupancy.
    """
    cache = getattr(ai, 'bench_expansion_stations', None)
    if cache is None:
        cache = ai.bench_expansion_stations = {}
    pathable = getattr(ai, 'in_pathing_grid', None)
    height = getattr(ai, 'get_terrain_height', None)
    bounds = getattr(ai, 'in_map_bounds', None)
    structures = list(getattr(ai, 'structures', ()) or ())
    resources = list(getattr(ai, 'mineral_field', ()) or ()) + list(getattr(ai, 'vespene_geyser', ()) or ())
    def usable(point):
        if callable(bounds) and not bounds(point):
            return False
        if callable(height) and height(point) != height(center):
            return False
        if callable(pathable) and not all(pathable(point.offset(offset)) for offset in
                ((0,0),(1,0),(-1,0),(0,1),(0,-1))):
            return False
        return (not overlaps_townhall_site(point, center, 5)
                and not any(point.distance_to(b.position) < float(getattr(b, 'radius', 1.5))+3
                            for b in structures+resources))
    old = cache.get(tuple(center))
    if old is not None and usable(old):
        return old
    nearby = [r.position for r in resources if r.position.distance_to(center) < 12]
    if nearby:
        minerals = Point2((sum(p.x for p in nearby)/len(nearby), sum(p.y for p in nearby)/len(nearby)))
        desired = center.towards(minerals, -8) if minerals.distance_to(center) > .1 else center.offset((8,0))
    else:
        desired = center.offset((8,0))
    candidates = [center.offset((x,y)) for x in range(-11,12) for y in range(-11,12)
                  if x*x+y*y <= 121]
    candidates = [p for p in candidates if usable(p)]
    if not candidates:
        # Constrained terrain still gets the closest walkable point off the site.
        candidates = [center.offset((x,y)) for x in range(-11,12) for y in range(-11,12)
                      if not overlaps_townhall_site(center.offset((x,y)), center, 1.5)
                      and (not callable(bounds) or bounds(center.offset((x,y))))
                      and (not callable(pathable) or pathable(center.offset((x,y))))
                      and (not callable(height) or height(center.offset((x,y))) == height(center))]
    point = min(candidates, key=lambda p:(p.distance_to(desired), p.x, p.y)) if candidates else center
    cache[tuple(center)] = point
    return point


def holding_region(ai, zone, fallback):
    """Flood-fill the base's connected, walkable terrain level."""
    center = getattr(zone, "center_location", None) or fallback
    cache = getattr(ai, "bench_holding_regions", None)
    if cache is None:
        cache = ai.bench_holding_regions = {}
    key = tuple(center)
    if key in cache:
        return cache[key]
    pathable = getattr(ai, "in_pathing_grid", None)
    height = getattr(ai, "get_terrain_height", None)
    level = height(center) if callable(height) else None
    def usable(x, y):
        point = Point2((x+.5,y+.5))
        in_bounds=getattr(ai,"in_map_bounds",None)
        if callable(in_bounds) and not in_bounds(point):return False
        return (point.distance_to(center)<=38 and (not callable(pathable) or pathable(point))
                and (level is None or height(point)==level))
    seeds = [center]+[center.offset((x,y)) for distance in (4,6,8)
                      for x,y in ((distance,0),(-distance,0),(0,distance),(0,-distance))]
    seed = next(((floor(p.x),floor(p.y)) for p in seeds if usable(floor(p.x),floor(p.y))),None)
    if seed is None:
        return []
    pending=deque([seed]);seen={seed};region=[]
    while pending:
        x,y=pending.popleft();region.append(Point2((x+.5,y+.5)))
        for neighbor in ((x+1,y),(x-1,y),(x,y+1),(x,y-1)):
            if neighbor not in seen:
                seen.add(neighbor)
                if usable(*neighbor):pending.append(neighbor)
    cache[key]=region
    return region


def base_ramps(ai, zone, fallback):
    center = getattr(zone,"center_location",None) or fallback
    cache=getattr(ai,"bench_holding_exits",None)
    if cache is None:cache=ai.bench_holding_exits={}
    key=tuple(center)
    if key in cache:return cache[key]
    named = getattr(zone,"ramp",None)
    ramps = list(getattr(getattr(ai,"game_info",None),"map_ramps",()) or ())
    if named is not None:ramps.append(named)
    height = getattr(ai,"get_terrain_height",None)
    region=holding_region(ai,zone,fallback)
    # Read actual walkable height transitions as well: one metadata ramp can
    # contain separate mouths, and blocked map decorations can hide a ramp.
    mouths=[]
    pathable=getattr(ai,"in_pathing_grid",None)
    if callable(height) and callable(pathable):
        level=height(center);edges={}
        for point in region:
            lower=[point.offset(offset) for offset in ((1,0),(-1,0),(0,1),(0,-1))
                   if pathable(point.offset(offset)) and 0<abs(level-height(point.offset(offset)))<=8]
            if lower:edges[tuple(point)]=lower
        while edges:
            seed=next(iter(edges));pending=[seed];upper=[];lower=[]
            while pending:
                point=pending.pop()
                if point not in edges:continue
                upper.append(point);lower.extend(edges.pop(point))
                x,y=point
                pending.extend((x+dx,y+dy) for dx in (-1,0,1) for dy in (-1,0,1) if dx or dy)
            if len(upper)>=2:
                top=Point2((sum(p[0] for p in upper)/len(upper),sum(p[1] for p in upper)/len(upper)))
                bottom=Point2((sum(p.x for p in lower)/len(lower),sum(p.y for p in lower)/len(lower)))
                if top.distance_to(bottom)>.1:mouths.append(SimpleNamespace(top_center=top,bottom_center=bottom))
    found={}
    for ramp in ramps:
        top,bottom=ramp.top_center,ramp.bottom_center
        if callable(height) and abs(height(center)-height(bottom))<=8 and abs(height(center)-height(top))>8:
            top,bottom=bottom,top
            ramp=SimpleNamespace(top_center=top,bottom_center=bottom)
        if top.distance_to(center)>38 or top.distance_to(bottom)<.1:
            continue
        if callable(height) and not (abs(height(center)-height(top))<=8):
            continue
        if not callable(height) and top.distance_to(center)>=bottom.distance_to(center):
            continue
        if not any(p.distance_to(top)<=4 for p in region):
            continue
        found[tuple(top)]=ramp
    if mouths:
        for ramp in list(found.values()):
            if not any(ramp.top_center.distance_to(m.top_center)<6 for m in mouths):mouths.append(ramp)
        found={tuple(r.top_center):r for r in mouths}
    ordered=sorted(found.values(),key=lambda r:r.top_center.distance_to(center))
    if ordered:
        # A shared plateau can extend into adjacent expansion sites. Keep the
        # entrances local to this base rather than averaging the whole plateau.
        reach=min(26,max(20,ordered[0].top_center.distance_to(center)+10))
        ordered=[r for r in ordered if r.top_center.distance_to(center)<=reach]
        enemies=list(getattr(ai,"enemy_start_locations",()) or ())
        if enemies:
            enemy=min(enemies,key=lambda p:p.distance_to(center))
            ex,ey=enemy.x-center.x,enemy.y-center.y
            length=(ex*ex+ey*ey)**.5
            forward=[r for r in ordered if ((r.top_center.x-center.x)*ex+(r.top_center.y-center.y)*ey)
                     >.15*length*r.top_center.distance_to(center)]
            if forward:ordered=forward
    cache[key]=ordered
    return cache[key]


def base_ramp(ai, zone, fallback):
    ramps=base_ramps(ai,zone,fallback)
    return ramps[0] if ramps else None


def ramp_slots(ai, zone, fallback):
    """Candidate centers for ONE army, preferably between the base's exits."""
    ramps=base_ramps(ai,zone,fallback)
    if not ramps:return []
    region=holding_region(ai,zone,fallback)
    if len(ramps)>1:
        desired=Point2((sum(r.top_center.x for r in ramps)/len(ramps),
                        sum(r.top_center.y for r in ramps)/len(ramps)))
    else:
        desired=ramps[0].top_center.towards(ramps[0].bottom_center,-2)
    pathable=getattr(ai,"in_pathing_grid",None)
    structures=list(getattr(ai,"structures",()) or ())
    candidates=[]
    for point in region:
        if point.distance_to(desired)>12:continue
        if any(point.distance_to(r.top_center)<3 for r in ramps):continue
        if callable(pathable) and not all(pathable(point.offset(offset)) for offset in ((0,0),(.75,0),(-.75,0),(0,.75),(0,-.75))):continue
        if any(point.distance_to(b.position)<float(getattr(b,"radius",1.5))+2 for b in structures):continue
        if len(ramps)==1:
            top,bottom=ramps[0].top_center,ramps[0].bottom_center
            length=top.distance_to(bottom);dx,dy=(top.x-bottom.x)/length,(top.y-bottom.y)/length
            depth=(point.x-top.x)*dx+(point.y-top.y)*dy
            side=-(point.x-top.x)*dy+(point.y-top.y)*dx
            if not (1.5<=depth<=7 and 2<=abs(side)<=8):continue
        # Favor an open patch without moving the station deep into the base.
        blocked=0
        for offset in ((3,0),(-3,0),(0,3),(0,-3)):
            p=point.offset(offset)
            if callable(pathable) and not pathable(p):blocked+=1
            if any(p.distance_to(b.position)<float(getattr(b,"radius",1.5))+1 for b in structures):blocked+=1
        candidates.append((point.distance_to(desired)+blocked*2,point))
    return [p for _,p in sorted(candidates,key=lambda item:(item[0],item[1].x,item[1].y))]


def high_ground_side(ai, zone, fallback, point):
    if not base_ramps(ai,zone,fallback):return True
    center=getattr(zone,"center_location",None) or fallback
    height=getattr(ai,"get_terrain_height",None)
    return not callable(height) or height(point)>=height(center)


def stable_ramp_station(ai, zone, fallback):
    """Keep the chosen area when troops temporarily occupy pathing tiles."""
    center=getattr(zone,"center_location",None) or fallback
    cache=getattr(ai,"bench_station_points",None)
    if cache is None:
        cache=ai.bench_station_points={}
    key=tuple(center)
    point=cache.get(key)
    if point is not None and not any(
            point.distance_to(building.position)<float(getattr(building,"radius",1.5))+1
            for building in getattr(ai,"structures",()) or ()):
        return point
    slots=ramp_slots(ai,zone,fallback)
    if not slots:
        cache.pop(key,None)
        return None
    cache[key]=slots[0]
    return slots[0]

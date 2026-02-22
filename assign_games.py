import sys
import random
import math
import heapq
from typing import List, Dict, Any, Tuple

# ---------- 核心算法（与之前相同）----------

def simulate(orders: List[List[int]], durations: List[float]) -> float:
    N = len(orders)
    M = len(durations)
    cur = [0] * N
    avail = [0.0] * M
    wait_queues = [[] for _ in range(M)]
    events = []

    for i in range(N):
        if cur[i] < M:
            j = orders[i][cur[i]]
            if avail[j] <= 0:
                start = 0.0
                end = start + durations[j]
                avail[j] = end
                heapq.heappush(events, (end, i, j))
                cur[i] += 1
            else:
                wait_queues[j].append(i)

    max_time = 0.0
    while events:
        t, i, j = heapq.heappop(events)
        max_time = max(max_time, t)

        if wait_queues[j]:
            next_i = wait_queues[j].pop(0)
            start = t
            end = start + durations[j]
            avail[j] = end
            heapq.heappush(events, (end, next_i, j))
            cur[next_i] += 1
        else:
            avail[j] = t

        if cur[i] < M:
            next_j = orders[i][cur[i]]
            if avail[next_j] <= t:
                start = t
                end = start + durations[next_j]
                avail[next_j] = end
                heapq.heappush(events, (end, i, next_j))
                cur[i] += 1
            else:
                wait_queues[next_j].append(i)

    return max_time

def random_solution(N: int, M: int) -> List[List[int]]:
    orders = []
    for _ in range(N):
        perm = list(range(M))
        random.shuffle(perm)
        orders.append(perm)
    return orders

def heuristic_solution(N: int, M: int, durations: List[float]) -> List[List[int]]:
    sorted_indices = sorted(range(M), key=lambda i: durations[i], reverse=True)
    orders = []
    for i in range(N):
        start = i % M
        order = sorted_indices[start:] + sorted_indices[:start]
        orders.append(order)
    return orders

def simulated_annealing(N: int, M: int, durations: List[float],
                        max_iter: int = 5000, initial_temp: float = None,
                        cooling_rate: float = 0.95) -> Tuple[List[List[int]], float]:
    current = heuristic_solution(N, M, durations)
    current_cost = simulate(current, durations)

    for _ in range(5):
        cand = random_solution(N, M)
        cost = simulate(cand, durations)
        if cost < current_cost:
            current = cand
            current_cost = cost

    best = [list(p) for p in current]
    best_cost = current_cost

    if initial_temp is None:
        initial_temp = current_cost * 10

    temp = initial_temp
    for _ in range(max_iter):
        new = [list(p) for p in current]
        person = random.randint(0, N - 1)
        pos1, pos2 = random.sample(range(M), 2)
        new[person][pos1], new[person][pos2] = new[person][pos2], new[person][pos1]

        new_cost = simulate(new, durations)
        delta = new_cost - current_cost

        if delta < 0 or random.random() < math.exp(-delta / temp):
            current = new
            current_cost = new_cost
            if current_cost < best_cost:
                best = [list(p) for p in current]
                best_cost = current_cost

        temp *= cooling_rate
        if temp < 1e-6:
            break

    return best, best_cost

def allocate_tasks(tasks: List[Dict[str, Any]], num_people: int) -> List[List[str]]:
    durations = [t['duration'] for t in tasks]
    names = [t['name'] for t in tasks]
    M = len(tasks)
    N = num_people

    if M == 0:
        return [[] for _ in range(N)]

    best_orders, _ = simulated_annealing(N, M, durations)

    result = []
    for person_orders in best_orders:
        result.append([names[i] for i in person_orders])
    return result

# ---------- 命令行入口 ----------

def parse_list_arg(arg: str):
    """解析可能带方括号的逗号分隔字符串，返回strip后的列表"""
    arg = arg.strip()
    if arg.startswith('[') and arg.endswith(']'):
        arg = arg[1:-1]
    if not arg:
        return []
    parts = arg.split(',')
    return [p.strip() for p in parts]

def main():
    if len(sys.argv) < 3:
        print("用法: python assign_tasks.py \"任务名1,任务名2,...\" \"时长1,时长2,...\" [人数]")
        print("示例: python assign_tasks.py \"火焰纹章,忍者龙剑传\" \"32,10.5\" 2")
        sys.exit(1)

    names_str = sys.argv[1]
    durations_str = sys.argv[2]
    num_people = int(sys.argv[3]) if len(sys.argv) >= 4 else 2

    names = parse_list_arg(names_str)
    durations = parse_list_arg(durations_str)

    if len(names) != len(durations):
        print("错误：任务名数量与时长数量不一致")
        sys.exit(1)

    try:
        durations = [float(d) for d in durations]
    except ValueError:
        print("错误：时长必须为数字")
        sys.exit(1)

    tasks = [{"name": n, "duration": d} for n, d in zip(names, durations)]

    allocation = allocate_tasks(tasks, num_people)

    for i, order in enumerate(allocation):
        print(f"{i}: {order}")

if __name__ == "__main__":
    main()
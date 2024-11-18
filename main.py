import requests
import json
import subprocess

PROMETHEUS_URL = "http://kube-prometheus-stack-prometheus.monitoring.svc:9090/api/v1/query"
ISTIO_INGRESS_NAMESPACE = "intern"
VIRTUAL_SERVICE_NAME = "sd-api-virtual"

# 사전에 정의된 GPU 모델 성능 - GPU 모델 명 기준으로 성능 지표를 걸어야함!
GPU_PERFORMANCE = {
    "node-1": 2.0,  # 성능이 좋은 GPU 모델에 더 높은 점수 부여
    "node-2": 1.5,
    "node-3": 1.0
}


def get_gpu_metrics():
    # GPU 메트릭 수집 쿼리
    gpu_usage_query = "dcgm_gpu_utilization"
    mem_usage_query = "dcgm_mem_copy_utilization"
    power_usage_query = "dcgm_power_usage"

    # Prometheus에서 데이터 가져오기
    gpu_usage = requests.get(f"{PROMETHEUS_URL}?query={gpu_usage_query}").json()
    mem_usage = requests.get(f"{PROMETHEUS_URL}?query={mem_usage_query}").json()
    power_usage = requests.get(f"{PROMETHEUS_URL}?query={power_usage_query}").json()

    return gpu_usage, mem_usage, power_usage


def calculate_node_score(gpu_perf, gpu_usage, mem_usage, power_usage):
    # 상태 점수 계산 (성능, 사용량, 메모리, 전력 소모 기반)
    performance_weight = 0.4
    usage_weight = 0.3
    mem_weight = 0.2
    power_weight = 0.1

    # GPU 사용량과 메모리 사용량, 전력 소모는 낮을수록 좋기 때문에 역으로 처리
    score = (
            performance_weight * gpu_perf -
            usage_weight * gpu_usage -
            mem_weight * mem_usage -
            power_weight * power_usage
    )
    return max(score, 0)  # 점수가 0보다 작아지지 않도록 설정


def update_virtual_service(weights):
    # VirtualService 가중치 업데이트를 위한 JSON Patch 생성
    routes = []
    total_score = sum(weights.values())

    for idx, (node, score) in enumerate(weights.items()):
        weight = int((score / total_score) * 100)  # 퍼센트 기반 가중치 계산
        routes.append({
            "op": "replace",
            "path": f"/spec/http/0/route/{idx}/weight",
            "value": weight
        })

    # JSON Patch를 적용하여 VirtualService 업데이트
    patch_payload = json.dumps(routes)
    try:
        subprocess.run(
            [
                "kubectl", "patch", "virtualservice", VIRTUAL_SERVICE_NAME,
                "-n", ISTIO_INGRESS_NAMESPACE, "--type=json", "-p", patch_payload
            ],
            check=True
        )
        print("VirtualService updated successfully!")
    except subprocess.CalledProcessError as e:
        print("Error updating VirtualService:")
        print(e.stderr)


def main():
    gpu_usage, mem_usage, power_usage = get_gpu_metrics()

    weights = {}

    # GPU 노드 정보 수집 및 점수 계산
    for node_name, gpu_perf in GPU_PERFORMANCE.items():
        # GPU 사용량 가져오기
        gpu_used = next(
            (float(m["value"][1]) for m in gpu_usage["data"]["result"] if m["metric"]["instance"] == node_name),
            0
        )

        # 메모리 사용량 가져오기
        mem_used = next(
            (float(m["value"][1]) for m in mem_usage["data"]["result"] if m["metric"]["instance"] == node_name),
            0
        )

        # 전력 소모량 가져오기
        power_used = next(
            (float(p["value"][1]) for p in power_usage["data"]["result"] if p["metric"]["instance"] == node_name),
            0
        )

        # 상태 점수 계산
        score = calculate_node_score(gpu_perf, gpu_used, mem_used, power_used)

        # 점수를 기반으로 가중치 설정 (점수가 높을수록 가중치 큼)
        weights[node_name] = score

    # VirtualService 업데이트
    update_virtual_service(weights)


if __name__ == "__main__":
    main()

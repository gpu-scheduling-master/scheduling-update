import requests
import json
import subprocess

PROMETHEUS_URL = "http://kube-prometheus-stack-prometheus.monitoring.svc:9090/api/v1/query"
ISTIO_INGRESS_NAMESPACE = "intern"
VIRTUAL_SERVICE_NAME = "sd-api-virtual"

# 사전에 정의된 GPU 모델 성능 - GPU 모델 명 기준으로 성능 지표를 걸어야함!
GPU_PERFORMANCE = {
    "NVIDIA GeForce RTX 3070": 1.0,  # 성능이 좋은 GPU 모델에 더 높은 점수 부여
    "NVIDIA GeForce RTX 4070": 1.5,
    "NVIDIA A10": 2.0  # 나중에 사용할 수 있는 데이터 센터 급의 gpu이름으로 변환
}


def get_gpu_metrics():
    # GPU 메트릭 수집 쿼리
    gpu_usage_query = "DCGM_FI_DEV_GPU_UTIL{}"
    mem_usage_query = "DCGM_FI_DEV_MEM_COPY_UTIL{}"
    power_usage_query = "DCGM_FI_DEV_POWER_USAGE{}"

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

    match = {"pod_name": "label number like 1, 2, 3, ...", }

    for node in gpu_usage["data"]["result"]:
        pod_name = node["metric"]["pod"]
        if pod_name not in match.keys():
            continue

        gpu_perf = GPU_PERFORMANCE.get(node["metric"]["modelName"], 1.0)  # 사전에 정의된 성능
        gpu_used = float(node["value"][1])

        # GPU 메모리 및 전력 소모 정보 가져오기
        mem_used = next((m["value"][1] for m in mem_usage["data"]["result"] if m["metric"]["instance"] == pod_name), 0)
        power_used = next(
            (p["value"][1] for p in power_usage["data"]["result"] if p["metric"]["instance"] == pod_name), 0)

        # 상태 점수 계산
        score = calculate_node_score(gpu_perf, gpu_used, float(mem_used), float(power_used))

        # 점수를 기반으로 가중치 설정 (점수가 높을수록 가중치 큼)
        weights[match[pod_name]] = score

    # VirtualService 업데이트
    update_virtual_service(weights)


if __name__ == "__main__":
    main()

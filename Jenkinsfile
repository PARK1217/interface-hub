pipeline {
    agent any

    options {
        timeout(time: 30, unit: 'MINUTES')
        buildDiscarder(logRotator(numToKeepStr: '20', daysToKeepStr: '30'))
        disableConcurrentBuilds()
        timestamps()
    }

    environment {
        PROJECT_DIR = "/home/park/interface-hub"
        APP_URL = "https://interface-hub.chobihome.site"
    }

    stages {
        stage('Checkout') {
            steps {
                echo "📦 GitHub에서 최신 코드 가져오기..."
                checkout scm
            }
        }

        stage('Verify .env') {
            steps {
                echo "🔍 .env 파일 점검..."
                sh '''
                    if [ ! -f ${PROJECT_DIR}/backend/.env ]; then
                        echo "❌ ${PROJECT_DIR}/backend/.env 가 없습니다."
                        exit 1
                    fi
                    echo "✓ .env 존재"
                '''
            }
        }

        stage('Sync Source') {
            steps {
                echo "🔄 운영 디렉토리에 최신 코드 복사..."
                sh '''
                    set +e
                    rsync -rlpD --delete --no-times \
                        --exclude='.git' \
                        --exclude='backend/.env' \
                        --exclude='node_modules' \
                        --exclude='__pycache__' \
                        --exclude='*.pyc' \
                        --exclude='.idea' \
                        ${WORKSPACE}/ ${PROJECT_DIR}/
                    set -e
                    echo "✓ 동기화 완료"
                '''
            }
        }

        stage('Docker Build & Deploy') {
            steps {
                echo "🐳 컨테이너 재빌드 + 재기동..."
                sh '''
                    cd ${PROJECT_DIR}
                    docker compose build backend frontend
                    docker compose up -d backend frontend
                    echo "✓ 재기동 완료"
                '''
            }
        }

        stage('Health Check') {
            steps {
                echo "💚 서비스 응답 확인..."
                sh '''
                    sleep 15
                    
                    OK=0
                    for i in 1 2 3 4 5 6; do
                        if curl -fsS http://localhost:8000/docs > /dev/null 2>&1; then
                            echo "✓ Backend OK"
                            OK=1
                            break
                        fi
                        echo "  Backend 대기 중 ($i/6)..."
                        sleep 5
                    done
                    [ $OK -eq 1 ] || { echo "❌ Backend 응답 없음"; exit 1; }
                    
                    OK=0
                    for i in 1 2 3 4 5 6; do
                        if curl -fsS http://localhost:5173/ > /dev/null 2>&1; then
                            echo "✓ Frontend OK"
                            OK=1
                            break
                        fi
                        echo "  Frontend 대기 중 ($i/6)..."
                        sleep 5
                    done
                    [ $OK -eq 1 ] || { echo "❌ Frontend 응답 없음"; exit 1; }
                    
                    echo "✓ 모든 서비스 정상"
                '''
            }
        }
    }

    post {
        success { echo "🎉 배포 성공! ${APP_URL}" }
        failure { echo "❌ 배포 실패. Console Output 확인 필요." }
        always {
            sh '''
                echo "─── 컨테이너 상태 ───"
                cd ${PROJECT_DIR} && docker compose ps 2>/dev/null || true
            '''
        }
    }
}

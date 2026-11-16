# ==============================================================================
# Autonomous Job Finder & Auto-Apply Frontend (Next.js App Router) Dockerfile
# ==============================================================================
FROM node:20-alpine

WORKDIR /app

COPY package.json ./
RUN npm install

COPY . .

EXPOSE 3000

CMD ["npm", "run", "dev"]

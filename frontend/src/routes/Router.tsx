import { Navigate, Route, Routes } from 'react-router-dom'
import { Landing } from './Landing'
import { Agent } from './Agent'
import { Ingestion } from './Ingestion'
import { ToolPosts } from './ToolPosts'
import { ToolAuthorsMentioning } from './ToolAuthorsMentioning'
import { ToolAuthorActivity } from './ToolAuthorActivity'
import { ToolAuthorsConnected } from './ToolAuthorsConnected'
import { ToolTopicCooc } from './ToolTopicCooc'
import { ToolNetwork } from './ToolNetwork'
import { ToolSocial } from './ToolSocial'
import { ToolExplorer } from './ToolExplorer'

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/agent" element={<Agent />} />
      <Route path="/ingestion" element={<Ingestion />} />
      <Route path="/tools/posts-mentioning" element={<ToolPosts />} />
      <Route path="/tools/authors-mentioning" element={<ToolAuthorsMentioning />} />
      <Route path="/tools/author-activity" element={<ToolAuthorActivity />} />
      <Route path="/tools/authors-connected" element={<ToolAuthorsConnected />} />
      <Route path="/tools/topic-cooccurrence" element={<ToolTopicCooc />} />
      <Route path="/tools/network-around" element={<ToolNetwork />} />
      <Route path="/tools/social-network-around" element={<ToolSocial />} />
      <Route path="/tools/explorer" element={<ToolExplorer />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

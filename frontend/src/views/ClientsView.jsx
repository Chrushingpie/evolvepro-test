import React, { useState, useEffect } from 'react';
import axios from 'axios';

const ClientsView = () => {
  const [clients, setClients] = useState([]);
  const [isLoading, setIsLoading] = useState(true); // New loading state

  useEffect(() => {
    const load = async () => {
      try {
        const response = await axios.get('/api/clients');
        setClients(response.data);
      } catch (error) {
        console.error('Failed to fetch clients:', error);
      } finally {
        setIsLoading(false); // Set loading state to false
      }
    };

    load();
  }, []);

  if (isLoading) { // Show loading text while fetching data
    return <div>Loading...</div>;
  }

  return (
    <div>
      <h1>Clients</h1>
      <ul>
        {clients.map(client => (
          <li key={client.id}>{client.name}</li>
        ))}
      </ul>
    </div>
  );
};

export default ClientsView;
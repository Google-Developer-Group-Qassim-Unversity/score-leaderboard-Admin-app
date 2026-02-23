"use client";

import * as React from "react";
import { useAuth } from "@clerk/nextjs";
import { Award, Plus, Trash2, Send, AlertCircle, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";

import { getCertificateEvents, sendManualCertificates } from "@/lib/api";
import type { Event, CertificateMember, CertificateJobResponse } from "@/lib/api-types";

export default function CertificatesPage() {
  const { getToken } = useAuth();

  const [events, setEvents] = React.useState<Event[]>([]);
  const [isLoading, setIsLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);

  const [selectedEventId, setSelectedEventId] = React.useState<string>("");
  const [members, setMembers] = React.useState<CertificateMember[]>([
    { name: "", email: "", gender: "Male" },
  ]);
  const [isSubmitting, setIsSubmitting] = React.useState(false);
  const [jobResult, setJobResult] = React.useState<CertificateJobResponse | null>(null);

  React.useEffect(() => {
    async function fetchEvents() {
      setIsLoading(true);
      setError(null);
      const response = await getCertificateEvents(getToken);
      if (response.success) {
        setEvents(response.data);
      } else {
        setError(response.error.message);
      }
      setIsLoading(false);
    }
    fetchEvents();
  }, [getToken]);

  const handleMemberChange = (index: number, field: keyof CertificateMember, value: string) => {
    const updated = [...members];
    updated[index] = { ...updated[index], [field]: value };
    setMembers(updated);
  };

  const addMember = () => {
    setMembers([...members, { name: "", email: "", gender: "Male" }]);
  };

  const removeMember = (index: number) => {
    if (members.length > 1) {
      setMembers(members.filter((_, i) => i !== index));
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!selectedEventId) {
      toast.error("Please select an event");
      return;
    }

    const validMembers = members.filter((m) => m.name.trim() && m.email.trim());
    if (validMembers.length === 0) {
      toast.error("Please add at least one member with name and email");
      return;
    }

    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    for (const member of validMembers) {
      if (!emailRegex.test(member.email)) {
        toast.error(`Invalid email: ${member.email}`);
        return;
      }
    }

    setIsSubmitting(true);
    setJobResult(null);

    const response = await sendManualCertificates(
      parseInt(selectedEventId),
      validMembers,
      getToken
    );

    if (response.success) {
      toast.success("Certificates sent successfully!");
      setJobResult(response.data);
    } else {
      toast.error(response.error.message);
    }

    setIsSubmitting(false);
  };

  const selectedEvent = events.find((e) => e.id.toString() === selectedEventId);

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Send Certificates</h1>
        <p className="text-muted-foreground mt-2">
          Send certificates to custom recipients for an event
        </p>
      </div>

      {isLoading && (
        <div className="space-y-4">
          <Skeleton className="h-12 w-full max-w-md" />
          <Skeleton className="h-64 w-full max-w-2xl" />
        </div>
      )}

      {error && (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertTitle>Failed to Load Events</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {!isLoading && !error && (
        <form onSubmit={handleSubmit} className="space-y-6">
          <Card className="max-w-2xl">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10">
                  <Award className="h-5 w-5 text-primary" />
                </div>
                Certificate Details
              </CardTitle>
              <CardDescription>
                Select an event and add recipients for the certificates
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              <div className="space-y-2">
                <Label htmlFor="event">Event</Label>
                <Select value={selectedEventId} onValueChange={setSelectedEventId}>
                  <SelectTrigger id="event" className="w-full">
                    <SelectValue placeholder="Select an event" />
                  </SelectTrigger>
                  <SelectContent>
                    {events.map((event) => (
                      <SelectItem key={event.id} value={event.id.toString()}>
                        {event.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {selectedEvent && (
                  <p className="text-sm text-muted-foreground">
                    Official: {selectedEvent.is_official ? "Yes" : "No"} | 
                    Date: {new Date(selectedEvent.start_datetime).toLocaleDateString()}
                  </p>
                )}
              </div>

              <div className="space-y-4">
                <div className="flex items-center justify-between">
                  <Label>Recipients</Label>
                  <Button type="button" variant="outline" size="sm" onClick={addMember}>
                    <Plus className="h-4 w-4 mr-1" />
                    Add Recipient
                  </Button>
                </div>

                <div className="space-y-4">
                  {members.map((member, index) => (
                    <div key={index} className="grid grid-cols-12 gap-2 items-start p-3 border rounded-lg">
                      <div className="col-span-4 space-y-1">
                        <Label htmlFor={`name-${index}`} className="text-xs">
                          Name
                        </Label>
                        <Input
                          id={`name-${index}`}
                          value={member.name}
                          onChange={(e) => handleMemberChange(index, "name", e.target.value)}
                          placeholder="Full name"
                          required
                        />
                      </div>
                      <div className="col-span-4 space-y-1">
                        <Label htmlFor={`email-${index}`} className="text-xs">
                          Email
                        </Label>
                        <Input
                          id={`email-${index}`}
                          type="email"
                          value={member.email}
                          onChange={(e) => handleMemberChange(index, "email", e.target.value)}
                          placeholder="email@example.com"
                          required
                        />
                      </div>
                      <div className="col-span-3 space-y-1">
                        <Label htmlFor={`gender-${index}`} className="text-xs">
                          Gender
                        </Label>
                        <Select
                          value={member.gender}
                          onValueChange={(v) => handleMemberChange(index, "gender", v as "Male" | "Female")}
                        >
                          <SelectTrigger id={`gender-${index}`}>
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="Male">Male</SelectItem>
                            <SelectItem value="Female">Female</SelectItem>
                          </SelectContent>
                        </Select>
                      </div>
                      <div className="col-span-1 flex items-end justify-center pb-1">
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          onClick={() => removeMember(index)}
                          disabled={members.length === 1}
                          className="text-destructive hover:text-destructive"
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="flex justify-end">
                <Button type="submit" disabled={isSubmitting || !selectedEventId}>
                  {isSubmitting ? (
                    <>
                      <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                      Sending...
                    </>
                  ) : (
                    <>
                      <Send className="h-4 w-4 mr-2" />
                      Send Certificates
                    </>
                  )}
                </Button>
              </div>
            </CardContent>
          </Card>
        </form>
      )}

      {jobResult && (
        <Card className="max-w-2xl">
          <CardHeader>
            <CardTitle>Certificate Job Created</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-2 text-sm">
              <p><strong>Job ID:</strong> {jobResult.job_id}</p>
              <p><strong>Event:</strong> {jobResult.event_name}</p>
              <p><strong>Folder:</strong> {jobResult.folder_name}</p>
              <p><strong>Status:</strong> {jobResult.status}</p>
              <p><strong>Message:</strong> {jobResult.message}</p>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
